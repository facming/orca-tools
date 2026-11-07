'use strict';
const {DownloadManager}=require('./engine.cjs');
// Called only by Orca main after checking the closed, signed resource manifest.
module.exports={create(options){
 if(!options.hostTasks)throw Error('Main-owned lifecycle required');
 return new DownloadManager(options);
}};
