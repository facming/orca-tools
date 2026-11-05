'use strict';
const path=require('node:path'),cp=require('node:child_process');
const {ToolkitError}=require('./platform/errors.cjs');
function operation(root,name,operation,extra={}){
 // CPython binds openat/flock/renameat, absent from Node's fs API. A missing
 // interpreter fails closed. -I prevents user Python startup/module injection.
 const helper=path.join(__dirname,'private-json.py');
 const result=cp.spawnSync('/usr/bin/env',['python3','-I','-c',
  'import runpy,sys;sys.path.insert(0,sys.argv[1]);runpy.run_path(sys.argv[2],run_name="__main__")',__dirname,helper],
  {input:JSON.stringify({root:path.resolve(root),name,operation,...extra}),encoding:'utf8',timeout:10000,maxBuffer:8*1024*1024,
   env:{PATH:'/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin',LANG:'en_US.UTF-8'}});
 if(result.error||result.status!==0)throw new ToolkitError('Unsafe, changed, corrupt or inaccessible settings. Reload or repair the original configuration manually.');
 try{return JSON.parse(result.stdout);}catch{throw new ToolkitError('Invalid private settings helper response.');}
}
module.exports={read:(root,name)=>operation(root,name,'read'),write:(root,name,value,expected)=>operation(root,name,'write',{value,expected})};
