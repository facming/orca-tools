// Install manually in Assets/Editor after review. Trusted C# is NOT sandboxed.
// No listener, eval, shell, reflection dispatch, package installation or preferences writes.
#if UNITY_EDITOR_OSX
using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;
using UnityEditor;
using UnityEditor.SceneManagement;

[InitializeOnLoad]
public static class OrcaUnityBridge
{
    [Serializable] public class Request {
        public string operation, session, id_request, name, component, property, path;
        public bool authorized;
        public int id;
        public float value;
        public float[] position, rotation, scale;
    }
    [Serializable] public class Row {
        public int id, parent;
        public string name, path, type, text;
        public float[] position, rotation, scale;
    }
    [Serializable] public class Result {
        public string message, version, pipeline;
        public bool playing;
        public Row[] rows;
    }
    [Serializable] public class Response {
        public string session, id_request;
        public bool ok, unsupported;
        public Result result;
    }
    [Serializable] public class Health {
        public string session, project, version, pipeline;
        public int pid;
        public double timestamp;
        public bool metal, compatible;
    }
    static readonly string spool = Environment.GetEnvironmentVariable("ORCA_UNITY_SPOOL");
    static readonly string secret = Environment.GetEnvironmentVariable("ORCA_UNITY_SESSION");
    static readonly string project = Environment.GetEnvironmentVariable("ORCA_UNITY_PROJECT");
    static readonly Queue<Row> logs = new Queue<Row>();
    static double heartbeat;
    static Request captureRequest;
    static OrcaUnityBridge() {
        if (String.IsNullOrEmpty(spool) || String.IsNullOrEmpty(secret) || secret.Length != 64 ||
            Path.GetFullPath(Path.Combine(Application.dataPath, "..")) != project) return;
        try { NoLinks(spool); NoLinks(project); } catch { return; }
        EditorApplication.update += Update;
        Application.logMessageReceived += Log;
    }
    static void NoLinks(string path) {
        for (var p = new DirectoryInfo(Path.GetFullPath(path)); p != null; p = p.Parent)
            if ((p.Attributes & FileAttributes.ReparsePoint) != 0) throw new InvalidOperationException();
    }
    static bool Id(string value) { return value != null && System.Text.RegularExpressions.Regex.IsMatch(value, "^[a-f0-9]{32}$"); }
    static string FileFor(string id, string suffix) {
        if (!Id(id)) throw new InvalidOperationException();
        NoLinks(spool);
        return Path.Combine(spool, id + suffix);
    }
    static void WriteNew(string path, string text) {
        using (var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None))
        using (var writer = new StreamWriter(stream)) writer.Write(text);
    }
    static void Log(string message, string stack, LogType kind) {
        // Stacks omitted; backend redacts credentials, paths and host metadata again.
        logs.Enqueue(new Row {type=kind.ToString(), text=message.Length > 2048 ? message.Substring(0,2048) : message});
        while (logs.Count > 100) logs.Dequeue();
    }
    static Health State() {
        var pipeline = GraphicsSettings.currentRenderPipeline;
        var type = pipeline == null ? "BuiltIn" : pipeline.GetType().FullName;
        bool known = type == "BuiltIn" || type == "UnityEngine.Rendering.Universal.UniversalRenderPipelineAsset" ||
            type == "UnityEngine.Rendering.HighDefinition.HDRenderPipelineAsset";
        return new Health {session=secret, project=project, version=Application.unityVersion,
            pid=System.Diagnostics.Process.GetCurrentProcess().Id,
            timestamp=DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()/1000.0,
            metal=SystemInfo.graphicsDeviceType == GraphicsDeviceType.Metal, pipeline=type,
            compatible=known && (PlayerSettings.GetUseDefaultGraphicsAPIs(BuildTarget.StandaloneOSX) ||
                PlayerSettings.GetGraphicsAPIs(BuildTarget.StandaloneOSX).Contains(GraphicsDeviceType.Metal))};
    }
    static void Update() {
        try {
            NoLinks(spool);
            if (EditorApplication.timeSinceStartup > heartbeat) {
                heartbeat = EditorApplication.timeSinceStartup + 1;
                string tmp = Path.Combine(spool, Guid.NewGuid().ToString("N") + ".tmp");
                WriteNew(tmp, JsonUtility.ToJson(State()));
                string target = Path.Combine(spool, "health.json");
                if (File.Exists(target)) { if ((File.GetAttributes(target) & FileAttributes.ReparsePoint) != 0) throw new InvalidOperationException(); File.Delete(target); }
                File.Move(tmp, target);
            }
            if (captureRequest != null) {
                string output = FileFor(captureRequest.id_request, ".png");
                if (File.Exists(output)) {
                    if ((File.GetAttributes(output) & FileAttributes.ReparsePoint) != 0 || new FileInfo(output).Length > 32*1024*1024) throw new InvalidOperationException();
                    Reply(captureRequest, new Result {message="Game View captured"}, true, false);
                    captureRequest=null;
                }
                return;
            }
            if (EditorApplication.isCompiling || EditorApplication.isUpdating) return;
            foreach (string file in Directory.GetFiles(spool, "*.request.json").Take(1)) {
                if ((File.GetAttributes(file) & FileAttributes.ReparsePoint) != 0 || new FileInfo(file).Length > 65536) return;
                var request = JsonUtility.FromJson<Request>(File.ReadAllText(file));
                if (request == null || !request.authorized || request.session != secret || !Id(request.id_request) ||
                    file != FileFor(request.id_request, ".request.json")) return;
                File.Delete(file);
                try {
                    var state = State();
                    if (!state.metal || !state.compatible) throw new NotSupportedException();
                    if (request.operation == "gameview.capture") { Capture(request); return; }
                    Reply(request, Execute(request), true, false);
                } catch (NotSupportedException) { Reply(request, new Result {message="unsupported"}, false, true); }
                catch { Reply(request, new Result {message="Editor rejected operation; inspect local project."}, false, false); }
            }
        } catch { /* Fail closed: no healthy heartbeat or successful response on transport errors. */ }
    }
    static void Reply(Request r, Result result, bool ok, bool unsupported) {
        string tmp = FileFor(r.id_request, ".response.tmp");
        WriteNew(tmp, JsonUtility.ToJson(new Response {session=secret, id_request=r.id_request, result=result, ok=ok, unsupported=unsupported}));
        File.Move(tmp, FileFor(r.id_request, ".response.json"));
    }
    static GameObject ObjectFor(int id) {
        var go = EditorUtility.InstanceIDToObject(id) as GameObject;
        if (go == null || EditorUtility.IsPersistent(go) || !go.scene.IsValid() || !go.scene.isLoaded ||
            (go.hideFlags & HideFlags.DontSave) != 0) throw new InvalidOperationException();
        return go;
    }
    static float[] Vector(Vector3 v) { return new[] {v.x, v.y, v.z}; }
    static Vector3 Vector(float[] v) {
        if (v == null || v.Length != 3 || v.Any(x => float.IsNaN(x) || float.IsInfinity(x) || Math.Abs(x) > 100000)) throw new InvalidOperationException();
        return new Vector3(v[0], v[1], v[2]);
    }
    static Row Describe(GameObject go) {
        return new Row {id=go.GetInstanceID(), name=go.name, parent=go.transform.parent == null ? 0 : go.transform.parent.gameObject.GetInstanceID(),
            position=Vector(go.transform.localPosition), rotation=Vector(go.transform.localEulerAngles), scale=Vector(go.transform.localScale)};
    }
    static IEnumerable<GameObject> Objects() {
        for (int i=0; i<SceneManager.sceneCount; i++) {
            var scene=SceneManager.GetSceneAt(i);
            if (!scene.isLoaded) continue;
            foreach (var root in scene.GetRootGameObjects())
                foreach (var t in root.GetComponentsInChildren<Transform>(true))
                    if ((t.gameObject.hideFlags & HideFlags.DontSave) == 0) yield return t.gameObject;
        }
    }
    static bool SafeAsset(string path) {
        if (String.IsNullOrEmpty(path) || !path.StartsWith("Assets/") || path.Contains("\\") || path.Contains(":")) return false;
        if (path.Split('/').Any(p => p.Length == 0 || p.StartsWith(".") || p.StartsWith("OrcaBridge-") || p == "Editor" || p == "Library" || p == "Temp" || p == "Obj")) return false;
        try {
            string full = Path.GetFullPath(Path.Combine(project, path));
            NoLinks(Path.GetDirectoryName(full));
            return full.StartsWith(project + "/") && File.Exists(full) &&
                (File.GetAttributes(full) & FileAttributes.ReparsePoint) == 0 && new FileInfo(full).Length <= 32*1024*1024;
        } catch { return false; }
    }
    static Result Execute(Request r) {
        var result = new Result {message="ok", playing=EditorApplication.isPlaying, version=Application.unityVersion};
        switch (r.operation) {
        case "editor.status": result.pipeline=State().pipeline; break;
        case "scene.get": result.rows=Enumerable.Range(0,SceneManager.sceneCount).Select(i => SceneManager.GetSceneAt(i)).Select(s=>new Row {name=s.name, path=s.path}).ToArray(); break;
        case "hierarchy.get": result.rows=Objects().Take(1000).Select(Describe).ToArray(); break;
        case "gameobject.get": result.rows=new[] {Describe(ObjectFor(r.id))}; break;
        case "gameobject.create":
            if (String.IsNullOrEmpty(r.name) || r.name.Length > 128 || r.name.Any(Char.IsControl)) throw new InvalidOperationException();
            var created=new GameObject(r.name); Undo.RegisterCreatedObjectUndo(created,"Orca create"); result.rows=new[] {Describe(created)}; break;
        case "gameobject.delete": Undo.DestroyObjectImmediate(ObjectFor(r.id)); break;
        case "transform.set":
            var t=ObjectFor(r.id).transform; Undo.RecordObject(t,"Orca transform");
            if (r.position != null && r.position.Length != 0) t.localPosition=Vector(r.position);
            if (r.rotation != null && r.rotation.Length != 0) t.localEulerAngles=Vector(r.rotation);
            if (r.scale != null && r.scale.Length != 0) t.localScale=Vector(r.scale);
            EditorUtility.SetDirty(t); break;
        case "component.add":
            var go=ObjectFor(r.id);
            if (r.component == "Camera") Undo.AddComponent<Camera>(go);
            else if (r.component == "Light") Undo.AddComponent<Light>(go);
            else if (r.component == "BoxCollider") Undo.AddComponent<BoxCollider>(go);
            else throw new NotSupportedException(); break;
        case "component.set": SetComponent(r); break;
        case "inspector.get":
            var obj=ObjectFor(r.id);
            var rows=new List<Row> {Describe(obj)};
            foreach (var c in obj.GetComponents<Component>()) {
                if (c == null) continue;
                string text="properties unsupported";
                if (c is Camera camera) text="field of view="+camera.fieldOfView+"; orthographic size="+camera.orthographicSize;
                if (c is Light light) text="intensity="+light.intensity+"; range="+light.range;
                if (c is BoxCollider box) text="is trigger="+box.isTrigger;
                rows.Add(new Row {type=c.GetType().Name,text=text});
            }
            result.rows=rows.ToArray(); break;
        case "scene.save":
            // Backend exclusively creates this task directory before sending the request.
            // SaveScene requires a project-relative path. Preserve staging for recovery.
            string destination="Assets/OrcaBridge-"+r.id_request+"/scene.unity";
            string absolute=Path.Combine(project,destination);
            NoLinks(Path.GetDirectoryName(absolute));
            if (!Directory.Exists(Path.GetDirectoryName(absolute)) || File.Exists(absolute) || File.Exists(absolute+".meta") ||
                !EditorSceneManager.SaveScene(SceneManager.GetActiveScene(), destination, true)) throw new InvalidOperationException();
            break;
        case "play.start": EditorApplication.isPlaying=true; result.message="Play transition requested; poll editor.status."; break;
        case "play.stop": EditorApplication.isPlaying=false; result.message="Play transition requested; poll editor.status."; break;
        case "console.get": result.rows=logs.ToArray(); break;
        case "assets.list": result.rows=AssetDatabase.GetAllAssetPaths().Where(SafeAsset).Where(p=>new[]{".png",".jpg",".jpeg",".obj",".unity"}.Contains(Path.GetExtension(p).ToLowerInvariant())).Take(1000).Select(p=>new Row {path=p}).ToArray(); break;
        case "asset.import":
            if (!SafeAsset(r.path) || !new[]{".png",".jpg",".jpeg",".obj"}.Contains(Path.GetExtension(r.path).ToLowerInvariant())) throw new NotSupportedException();
            AssetDatabase.ImportAsset(r.path, ImportAssetOptions.ForceUpdate); break;
        default: throw new NotSupportedException();
        }
        return result;
    }
    static void SetComponent(Request r) {
        if (float.IsNaN(r.value) || float.IsInfinity(r.value)) throw new InvalidOperationException();
        var go=ObjectFor(r.id);
        Component c=null;
        if (r.component == "Camera") c=go.GetComponent<Camera>();
        if (r.component == "Light") c=go.GetComponent<Light>();
        if (r.component == "BoxCollider") c=go.GetComponent<BoxCollider>();
        if (c == null) throw new NotSupportedException();
        Undo.RecordObject(c,"Orca component");
        if (c is Camera camera && r.property == "field of view" && r.value>=1 && r.value<=179) camera.fieldOfView=r.value;
        else if (c is Camera camera2 && r.property == "orthographic size" && r.value>=.01 && r.value<=10000) camera2.orthographicSize=r.value;
        else if (c is Light light && r.property == "intensity" && r.value>=0 && r.value<=10000) light.intensity=r.value;
        else if (c is Light light2 && r.property == "range" && r.value>=.01 && r.value<=10000) light2.range=r.value;
        else if (c is BoxCollider box && r.property == "is trigger" && (r.value==0 || r.value==1)) box.isTrigger=r.value==1;
        else throw new NotSupportedException();
        EditorUtility.SetDirty(c);
    }
    static void Capture(Request r) {
        // Unity Game View only, at frame end. Never desktop/mouse/keyboard capture.
        if (!EditorApplication.isPlaying || Screen.width>4096 || Screen.height>4096) throw new NotSupportedException();
        string output=FileFor(r.id_request,".png");
        if (File.Exists(output)) throw new InvalidOperationException();
        captureRequest=r;
        ScreenCapture.CaptureScreenshot(output);
    }
}
#endif
