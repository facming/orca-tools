'use strict';
// Proposed Host API v1. Presence is feature detection, never caller authentication.
const {ToolkitError}=require('../platform/errors.cjs');
const {createClient}=require('./client.cjs');
const METHOD='toolkit.configuration.v1';
const HOST_REQUIRED='This Orca Host does not provide trusted Toolkit configuration access. A sealed Host integration is required.';
function createHostClient(api){
 return createClient(async request=>{
  if(!api.host?.has?.(METHOD))throw new ToolkitError(HOST_REQUIRED);
  let response;
  try{response=await api.host.call(METHOD,{version:1,request});}
  catch{throw new ToolkitError('Trusted configuration Host unavailable or access denied. Reload and retry with the same request_id if a result is unknown.');}
  if(response?.ok===false&&typeof response.error==='string')throw new ToolkitError(response.error);
  if(response?.ok!==true||!response.result||typeof response.result!=='object')throw new ToolkitError('Invalid trusted configuration Host response.');
  return response.result;
 });
}
module.exports={createHostClient,METHOD,HOST_REQUIRED};
