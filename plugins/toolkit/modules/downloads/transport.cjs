'use strict';
const {ToolkitError} = require('../../platform/errors.cjs');
const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');
const zlib = require('node:zlib');
const {promisify} = require('node:util');
const execFile = promisify(cp.execFile);
const crypto = require('node:crypto');

// Both transports execute the exact same reader and lock protocol.
function remoteModule(){
 const files={'private_files.py':fs.readFileSync(path.join(__dirname,'../../private_files.py')),'remote-reader.py':fs.readFileSync(path.join(__dirname,'remote-reader.py'))};
 const revision=crypto.createHash('sha256').update(Buffer.concat(Object.values(files))).digest('hex');
 return [
  'import pathlib,base64,types,sys,json',
  "support=types.ModuleType('private_files');sys.modules['private_files']=support",
  `exec(base64.b64decode(${JSON.stringify(files['private_files.py'].toString('base64'))}),support.__dict__)`,
  `root=pathlib.Path.home()/'.local/share/orca-parallel-download'/"runtime-${revision}"`,
  'directory=support.PrivateDirectory(str(root))',
  'try:',
  ' with directory.lock(".install.lock"):',
  ...Object.entries(files).flatMap(([name,bytes])=>[
   `  name=${JSON.stringify(name)};content=base64.b64decode(${JSON.stringify(bytes.toString('base64'))})`,
   '  existing,version=directory.read(name)',
   "  if existing is not None and existing!=content: raise ValueError('Reader installation changed')",
   '  if existing is None: directory.replace(name,content,version)'
  ]),
  'finally: directory.close()',
  "m=types.ModuleType('download_reader');m.__file__=str(root/'remote-reader.py')",
  `exec(base64.b64decode(${JSON.stringify(files['remote-reader.py'].toString('base64'))}),m.__dict__)`
 ].join('\n');
}
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const {privateDirectory,privatePath}=require('./local-state.cjs');
const {validRemote,rootsFrom,contains}=require('./projects.cjs');
const {validate}=require('./settings.cjs');

class SshTransport {
  constructor({dataDir, runtime, environment, sshHost, sshUser = 'workspace', sshPort = 22, execute=execFile, spawn=cp.spawn, wait=delay, onConnection=()=>{}}) {
    if (!runtime) throw new ToolkitError('Downloads require the Toolkit runtime.');
    validate({environment,sshHost,sshUser,sshPort});
    Object.assign(this,{execute,spawn,wait,onConnection});
    Object.assign(this, {dataDir, runtime, cli: runtime.cli, environment, sshHost, sshUser, sshPort});
  }
  async orca(args, signal) {
    let stdout;
    try { ({stdout} = await this.execute(this.cli, [...args, '--environment', this.environment, '--json'],
      {signal,env:this.runtime.childEnv,windowsHide: true, timeout: 30000, maxBuffer: 2 * 1024 * 1024})); }
    catch { this.onConnection('offline');throw Object.assign(new ToolkitError('Unable to reach Orca. Check that Orca and the remote connection are open.'),{retryable:true}); }
    let response;try{response=JSON.parse(stdout);}catch{throw new ToolkitError('Invalid Orca response.');}
    if (!response.ok) {
      const code=String(response.error?.code||response.code||'').toLowerCase();
      const retryable=['timeout','request_timeout','etimedout','disconnected','connection_closed','econnreset','econnrefused','enotfound','enetunreach'].includes(code);
      throw Object.assign(new ToolkitError(retryable?'Orca connection interrupted. Reconnecting authorization.':'Orca request failed. Check the remote environment and permissions.'),{retryable});
    }
    return response.result;
  }
  directory(job) { if(!/^[a-f0-9]{32}$/.test(job.id))throw new ToolkitError('Invalid transfer ID');return path.join(this.dataDir, 'keys', job.id); }
  async prepare(job) {
    if(this.runtime.platform==='darwin')throw new ToolkitError('macOS requires v3 authorization; legacy/manual cleanup.');
    if(!validRemote(job.source))throw new ToolkitError('Enter an absolute remote project file path.');
    const client=require('../../remote-runtime.cjs').createRemoteClient(this.runtime,this.environment);
    if(!client.isRemote)throw new ToolkitError('The selected Orca connection is not remote.');
    const roots=rootsFrom((await this.orca(['repo','list'])).repos);
    const root=roots.sort((a,b)=>b.length-a.length).find(root=>contains(root,job.source));
    if(!root)throw new ToolkitError('Select a file inside an authorized remote project.');
    const directory = this.directory(job);
    if(this.runtime.platform==='darwin'){privateDirectory(this.dataDir);privateDirectory(path.join(this.dataDir,'keys'));privateDirectory(directory);}
    else fs.mkdirSync(directory, {recursive: true, mode: 0o700});
    const key = path.join(directory, 'key');
    if (!fs.existsSync(key)) await this.execute(this.runtime.keygen, ['-q', '-t', 'ed25519', '-N', '', '-f', key,
      '-C', 'orca-download:' + job.id], {env:this.runtime.childEnv,windowsHide: true, timeout: 15000});
    if(this.runtime.platform==='darwin'){privatePath(key);const pub=fs.lstatSync(key+'.pub');if(!pub.isFile()||pub.uid!==process.getuid()||pub.nlink!==1)throw new ToolkitError('Invalid task public key.');}
    const publicKey = fs.readFileSync(key + '.pub', 'utf8').trim();
    if(!/^ssh-ed25519 [A-Za-z0-9+/=]+(?: [a-zA-Z0-9:_-]+)?$/.test(publicKey))throw new ToolkitError('Invalid task public key.');
    const request = {id: job.id, source: job.source, publicKey};
    const script = [
      remoteModule(),
      `request=json.loads(base64.b64decode(${JSON.stringify(Buffer.from(JSON.stringify(request)).toString('base64'))}))`,
      `allowed=pathlib.Path(${JSON.stringify(root)}).resolve(strict=True);source=pathlib.Path(request['source']).resolve(strict=True)`,
      "if source!=allowed and allowed not in source.parents: raise ValueError('Source outside project')",
      "request['source']=str(source)",
      'print("ORCA_DOWNLOAD_META="+json.dumps(m.register(request)),flush=True)'
    ].join('\n');
    const payload = zlib.deflateSync(Buffer.from(script)).toString('base64');
    const command = `python3 -c 'import base64,zlib;exec(zlib.decompress(base64.b64decode("${payload}")))'`;
    let handle;
    try {
      const result = await this.orca(['terminal', 'create', '--worktree', 'path:'+root,
        '--title', 'Prepare download', '--command', command]);
      handle = result.terminal?.handle;
      if (!handle) throw new ToolkitError('Orca did not create a preparation terminal');
      for (let attempt = 0; attempt < 60; attempt++) {
        await this.wait(1000);
        const result = await this.orca(['terminal', 'read', '--terminal', handle, '--limit', '30']);
        const line = result.terminal?.tail?.find(line => line.startsWith('ORCA_DOWNLOAD_META='));
        if (!line) {
          if (result.terminal?.tail?.some(line => line.includes('Traceback (most recent call last)')))
            throw new ToolkitError('Unable to prepare this file. Check that the remote path exists and is readable.');
          continue;
        }
        const meta = JSON.parse(line.slice('ORCA_DOWNLOAD_META='.length));
        if (!Number.isSafeInteger(meta.size) || meta.size < 0 || !/^[a-f0-9]{64}$/.test(meta.sha256)
          || !/^ssh-ed25519 [A-Za-z0-9+/=]+$/.test(meta.hostKey)) throw new ToolkitError('Invalid download metadata');
        const known=path.join(directory,'known_hosts'),pin=`${job.id} ${meta.hostKey}\n`;
        if(fs.existsSync(known)){if(this.runtime.platform==='darwin')privatePath(known);if(fs.readFileSync(known,'utf8')!==pin)throw new ToolkitError('SSH host key changed. Clear access and create a new task after verifying the server.');}
        else fs.writeFileSync(known,pin,{flag:'wx',mode:0o600});
        this.onConnection('ready');
        return meta;
      }
      throw new ToolkitError('Preparation timed out. Retry when Orca is connected.');
    } finally {
      if (handle) await this.orca(['terminal', 'close', '--terminal', handle]).catch(() => {});
    }
  }
  arguments(job) {
    const directory = this.directory(job);
    if(this.runtime.platform==='darwin'){privatePath(directory,true);privatePath(path.join(directory,'key'));privatePath(path.join(directory,'known_hosts'));}
    const nullFile=this.runtime.platform==='win32'?'NUL':'/dev/null';
    const known='"'+path.join(directory,'known_hosts').replaceAll('\\','\\\\').replaceAll('"','\\"')+'"';
    return ['-F',nullFile,'-T', '-p', String(this.sshPort), '-i', path.join(directory, 'key'),
      '-o', 'HostKeyAlias=' + job.id, '-o', 'ConnectTimeout=12', '-o', 'IdentitiesOnly=yes',
      '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
      '-o', 'UserKnownHostsFile=' + known, '-o', 'GlobalKnownHostsFile='+nullFile,
      '-o','IdentityAgent=none','-o','ClearAllForwardings=yes','-o','UpdateHostKeys=no',
      '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3', this.sshUser + '@' + this.sshHost];
  }
  read(job, offset, length, signal, onData) {
    return new Promise((resolve, reject) => {
      if(!Number.isSafeInteger(offset)||offset<0||!Number.isSafeInteger(length)||length<1||length>8*1024*1024)return reject(new ToolkitError('Invalid byte range'));
      if (signal.aborted) return reject(new ToolkitError('Paused'));
      const child = this.spawn(this.runtime.ssh, [...this.arguments(job), 'read', String(offset), String(length)],
        {env:this.runtime.childEnv,windowsHide: true, stdio: ['ignore', 'pipe', 'pipe']});
      let received = 0, settled = false, diagnostic='', idleTimer;
      const watchProgress=()=>{clearTimeout(idleTimer);idleTimer=setTimeout(()=>{child.kill();finish(new ToolkitError('No download data received for 30 seconds. Reconnecting the SSH download channel.'));},30000);};
      const abort = () => child.kill();
      signal.addEventListener('abort', abort, {once: true});
      const finish = error => {
        if (settled) return;
        settled = true;
        clearTimeout(idleTimer);
        signal.removeEventListener('abort', abort);
        if(!signal.aborted)this.onConnection(error?'offline':'ready');
        error ? reject(error) : resolve();
      };
      watchProgress();
      child.stdout.on('data', data => {
        if(settled)return;
        try {
          if (received + data.length > length) throw new ToolkitError('Invalid range response');
          onData(data, received); received += data.length;watchProgress();
        } catch (error) { child.kill(); finish(error); }
      });
      child.stderr.on('data',data=>{if(diagnostic.length<8192)diagnostic+=data.toString().slice(0,8192-diagnostic.length);});
      child.on('error', finish);
      child.on('close', code => finish(code === 0 && received === length ? null :
        signal.aborted?new ToolkitError('Paused'):/REMOTE HOST IDENTIFICATION HAS CHANGED|Host key verification failed/i.test(diagnostic)?Object.assign(new ToolkitError('SSH host identity changed. Verify the download endpoint before continuing.'),{retryable:false}):/Permission denied/i.test(diagnostic)?Object.assign(new ToolkitError('Download access was denied. Clear access and create a new task after checking the endpoint authorization.'),{retryable:false}):new ToolkitError('Transfer interrupted. Reconnecting the SSH download channel.')));
    });
  }
  async cleanup(job) {
    if(this.runtime.platform==='darwin')throw new ToolkitError('Legacy task: manual cleanup required; key retained.');
    try{const result=await this.execute(this.runtime.ssh, [...this.arguments(job), 'done'], {env:this.runtime.childEnv,windowsHide: true, timeout: 20000});if(result.stdout.trim()!=='KEY_REMOVED:'+job.id)throw Error();}
    catch{await this.cleanupViaOrca(job);}
    const directory = this.directory(job);
    for (const name of ['key', 'key.pub', 'known_hosts']) fs.rmSync(path.join(directory, name), {force: true});
    if(fs.existsSync(directory))fs.rmdirSync(directory);
  }
  async cleanupViaOrca(job) {
    if(this.runtime.platform==='darwin')throw new ToolkitError('Legacy task: manual cleanup required; key retained.');
    if(!/^[a-f0-9]{32}$/.test(job.id))throw new ToolkitError('Invalid transfer ID');
    const nonce=crypto.randomBytes(16).toString('hex');
    const receipt='ORCA_DOWNLOAD_CLEANED='+job.id+':'+nonce;
    const script=[
      remoteModule(),
      `result=m.cleanup(${JSON.stringify(job.id)})`,
      `if result!=${JSON.stringify('KEY_REMOVED:'+job.id)}: raise ValueError('Cleanup not confirmed')`,
      `print(${JSON.stringify(receipt)},flush=True)`
    ].join('\n');
    const payload=zlib.deflateSync(Buffer.from(script)).toString('base64');
    const command=`python3 -c 'import base64,zlib;exec(zlib.decompress(base64.b64decode("${payload}")))'`;
    const {repos=[]}=await this.orca(['repo','list']);
    const root=rootsFrom(repos)[0];
    if(!root)throw new ToolkitError('Open a remote project in Orca to clear transfer access.');
    let handle;
    try{
      const result=await this.orca(['terminal','create','--worktree','path:'+root,'--title','Clear transfer access','--command',command]);
      handle=result.terminal?.handle;if(!handle)throw new ToolkitError('Could not start access cleanup');
      for(let i=0;i<20;i++){
        await this.wait(1000);
        const output=await this.orca(['terminal','read','--terminal',handle,'--limit','10']);
        if(output.terminal?.tail?.includes(receipt))return;
      }
      throw new ToolkitError('Cleanup timed out. Retry when Orca is connected.');
    }finally{if(handle)await this.orca(['terminal','close','--terminal',handle]).catch(()=>{});}
  }
}
module.exports = {SshTransport,remoteModule};
