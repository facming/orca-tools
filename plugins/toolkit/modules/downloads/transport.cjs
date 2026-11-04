'use strict';
const {ToolkitError} = require('../../platform/errors.cjs');
const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');
const zlib = require('node:zlib');
const {promisify} = require('node:util');
const execFile = promisify(cp.execFile);
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

class SshTransport {
  constructor({dataDir, runtime, environment, sshHost, sshUser = 'workspace', sshPort = 22}) {
    if (!runtime) throw new ToolkitError('Downloads require the Toolkit runtime.');
    if (runtime.platform === 'darwin') throw new ToolkitError('Downloads are in preview on macOS.');
    Object.assign(this, {dataDir, runtime, cli: runtime.cli, environment, sshHost, sshUser, sshPort});
  }
  async orca(args) {
    let stdout;
    try { ({stdout} = await execFile(this.cli, [...args, '--environment', this.environment, '--json'],
      {env:this.runtime.childEnv,windowsHide: true, timeout: 65000, maxBuffer: 2 * 1024 * 1024})); }
    catch { throw new ToolkitError('Unable to reach Orca. Check that Orca and the remote connection are open.'); }
    const response = JSON.parse(stdout);
    if (!response.ok) throw new ToolkitError('Orca request failed.');
    return response.result;
  }
  directory(job) { return path.join(this.dataDir, 'keys', job.id); }
  async prepare(job) {
    const directory = this.directory(job);
    fs.mkdirSync(directory, {recursive: true, mode: 0o700});
    const key = path.join(directory, 'key');
    if (!fs.existsSync(key)) await execFile(this.runtime.keygen, ['-q', '-t', 'ed25519', '-N', '', '-f', key,
      '-C', 'orca-download:' + job.id], {env:this.runtime.childEnv,windowsHide: true, timeout: 15000});
    const publicKey = fs.readFileSync(key + '.pub', 'utf8').trim();
    const reader = fs.readFileSync(path.join(__dirname, 'remote-reader.py'));
    const request = {id: job.id, source: job.source, publicKey};
    const script = [
      'import pathlib,base64,importlib.util,json',
      'root=pathlib.Path.home()/"setup-orca";root.mkdir(exist_ok=True,mode=0o700)',
      `temp=root/"parallel-download-reader-${job.id}.tmp"`,
      `temp.write_bytes(base64.b64decode(${JSON.stringify(reader.toString('base64'))}))`,
      'temp.chmod(0o700);target=root/"parallel-download-reader.py";temp.replace(target)',
      'spec=importlib.util.spec_from_file_location("download_reader",target);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)',
      `request=json.loads(base64.b64decode(${JSON.stringify(Buffer.from(JSON.stringify(request)).toString('base64'))}))`,
      'print("ORCA_DOWNLOAD_META="+json.dumps(m.register(request)),flush=True)'
    ].join('\n');
    const payload = zlib.deflateSync(Buffer.from(script)).toString('base64');
    const command = `python3 -c 'import base64,zlib;exec(zlib.decompress(base64.b64decode("${payload}")))'`;
    let handle;
    try {
      const {repos=[]} = await this.orca(['repo','list']);
      const roots = repos.filter(repo=>typeof repo.path==='string'&&repo.path.startsWith('/'));
      roots.sort((a,b)=>b.path.length-a.path.length);
      const root = roots.find(repo=>job.source===repo.path||job.source.startsWith(repo.path+'/')) || roots[0];
      if(!root)throw new ToolkitError('Open a project on the remote server in Orca before downloading.');
      const result = await this.orca(['terminal', 'create', '--worktree', 'path:'+root.path,
        '--title', 'Prepare download', '--command', command]);
      handle = result.terminal?.handle;
      if (!handle) throw new ToolkitError('Orca did not create a preparation terminal');
      for (let attempt = 0; attempt < 60; attempt++) {
        await delay(1000);
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
        fs.writeFileSync(path.join(directory, 'known_hosts'), `${job.id} ${meta.hostKey}\n`);
        return meta;
      }
      throw new ToolkitError('Preparation timed out. Retry when Orca is connected.');
    } finally {
      if (handle) await this.orca(['terminal', 'close', '--terminal', handle]).catch(() => {});
    }
  }
  arguments(job) {
    const directory = this.directory(job);
    return ['-T', '-p', String(this.sshPort), '-i', path.join(directory, 'key'),
      '-o', 'HostKeyAlias=' + job.id, '-o', 'ConnectTimeout=12', '-o', 'IdentitiesOnly=yes',
      '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
      '-o', 'UserKnownHostsFile=' + path.join(directory, 'known_hosts'),
      '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3', this.sshUser + '@' + this.sshHost];
  }
  read(job, offset, length, signal, onData) {
    return new Promise((resolve, reject) => {
      if (signal.aborted) return reject(new ToolkitError('Paused'));
      const child = cp.spawn(this.runtime.ssh, [...this.arguments(job), 'read', String(offset), String(length)],
        {env:this.runtime.childEnv,windowsHide: true, stdio: ['ignore', 'pipe', 'pipe']});
      let received = 0, settled = false;
      const abort = () => child.kill();
      signal.addEventListener('abort', abort, {once: true});
      const finish = error => {
        if (settled) return;
        settled = true;
        signal.removeEventListener('abort', abort);
        error ? reject(error) : resolve();
      };
      child.stdout.on('data', data => {
        try {
          if (received + data.length > length) throw new ToolkitError('Invalid range response');
          onData(data, received); received += data.length;
        } catch (error) { child.kill(); finish(error); }
      });
      child.stderr.resume();
      child.on('error', finish);
      child.on('close', code => finish(code === 0 && received === length ? null :
        new ToolkitError(signal.aborted ? 'Paused' : 'Transfer interrupted. Check the SSH connection.')));
    });
  }
  async cleanup(job) {
    try{await execFile(this.runtime.ssh, [...this.arguments(job), 'done'], {env:this.runtime.childEnv,windowsHide: true, timeout: 20000});}
    catch{await this.cleanupViaOrca(job);}
    const directory = this.directory(job);
    for (const name of ['key', 'key.pub', 'known_hosts']) fs.rmSync(path.join(directory, name), {force: true});
    fs.rmdirSync(directory);
  }
  async cleanupViaOrca(job) {
    if(!/^[a-f0-9]{32}$/.test(job.id))throw new ToolkitError('Invalid transfer ID');
    const script=[
      'from pathlib import Path',
      `tag="orca-download:${job.id}"`,
      'keys=Path.home()/".ssh/authorized_keys"',
      'if keys.exists():',
      ' lines=keys.read_text().splitlines(keepends=True)',
      ` temp=keys.with_suffix(".cleanup-${job.id}")`,
      ' temp.write_text("".join(line for line in lines if tag not in line))',
      ' temp.chmod(0o600);temp.replace(keys)',
      `(Path.home()/".local/share/orca-parallel-download/jobs/${job.id}.json").unlink(missing_ok=True)`,
      `print("ORCA_DOWNLOAD_CLEANED=${job.id}",flush=True)`
    ].join('\n');
    const payload=zlib.deflateSync(Buffer.from(script)).toString('base64');
    const command=`python3 -c 'import base64,zlib;exec(zlib.decompress(base64.b64decode("${payload}")))'`;
    const {repos=[]}=await this.orca(['repo','list']);
    const root=repos.find(repo=>typeof repo.path==='string'&&repo.path.startsWith('/'));
    if(!root)throw new ToolkitError('Open a remote project in Orca to clear transfer access.');
    let handle;
    try{
      const result=await this.orca(['terminal','create','--worktree','path:'+root.path,'--title','Clear transfer access','--command',command]);
      handle=result.terminal?.handle;if(!handle)throw new ToolkitError('Could not start access cleanup');
      for(let i=0;i<20;i++){
        await delay(1000);
        const output=await this.orca(['terminal','read','--terminal',handle,'--limit','10']);
        if(output.terminal?.tail?.includes('ORCA_DOWNLOAD_CLEANED='+job.id))return;
      }
      throw new ToolkitError('Cleanup timed out. Retry when Orca is connected.');
    }finally{if(handle)await this.orca(['terminal','close','--terminal',handle]).catch(()=>{});}
  }
}
module.exports = {SshTransport};
