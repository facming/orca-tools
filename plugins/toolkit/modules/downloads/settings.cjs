'use strict';
const {ToolkitError}=require('../../platform/errors.cjs');
const net=require('node:net');
function validate(settings){
 if(typeof settings.environment!=='string'||!settings.environment.trim())throw new ToolkitError('Set the exact Orca remote environment name.');
 if(net.isIP(settings.sshHost)!==4)throw new ToolkitError('Set a literal Tailscale IPv4 address or 127.0.0.1 for an SSH tunnel.');
 const octets=settings.sshHost.split('.').map(Number);
 if(settings.sshHost!=='127.0.0.1'&&(octets[0]!==100||octets[1]<64||octets[1]>127))throw new ToolkitError('SSH target must be a Tailscale address or the local SSH tunnel.');
 if(typeof settings.sshUser!=='string'||!/^[-a-z_][-a-z0-9_]*[$]?$/i.test(settings.sshUser))throw new ToolkitError('Set the remote SSH user.');
 const sshPort=settings.sshPort??22;
 if(!Number.isInteger(sshPort)||sshPort<1||sshPort>65535)throw new ToolkitError('Invalid SSH port.');
 return {environment:settings.environment,sshHost:settings.sshHost,sshUser:settings.sshUser,sshPort};
}
module.exports={validate};
