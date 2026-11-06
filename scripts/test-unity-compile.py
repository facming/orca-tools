"""Compile the real extension against the installed 6000.5.4f1 API, without a license."""
from pathlib import Path
import subprocess
import tempfile
root = Path('/Applications/Unity/Hub/Editor/6000.5.4f1/Unity.app/Contents/Resources/Scripting')
output = Path(tempfile.mkdtemp(prefix='orca-unity-compile-'))
refs = list((root / 'UnityReferenceAssemblies/unity-4.8-api').glob('*.dll')) + list((root / 'UnityReferenceAssemblies/unity-4.8-api/Facades').glob('*.dll'))
refs += list((root / 'Managed/UnityEngine').glob('*.dll')) + list((root / 'Managed').glob('UnityEditor*.dll'))
# UnityEditor.dll exists in two directories; prefer the main editor assembly.
refs = list({p.name: p for p in refs if p.name != 'UnityEditor.dll'}.values())
args = ['-nologo', '-target:library', '-nostdlib+', '-define:UNITY_EDITOR_OSX,UNITY_6000_5_OR_NEWER', '-out:'+str(output/'OrcaUnityBridge.dll')]
args += ['-r:'+str(p) for p in refs]
args += [str(Path(__file__).resolve().parents[1]/'services/local-gpu-bridge/OrcaUnityBridge.cs')]
rsp = output/'compile.rsp'
rsp.write_text('\n'.join('"'+arg+'"' for arg in args))
subprocess.run([str(root/'DotNetSdk/dotnet'), str(next((root/'DotNetSdk/sdk').glob('*/Roslyn/bincore/csc.dll'))), '@'+str(rsp)], check=True)
print('PASS real installed Unity 6000.5.4f1 C# API compile: '+str(output))
