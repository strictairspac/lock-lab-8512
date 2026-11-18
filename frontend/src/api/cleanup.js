import {api} from './client.js';
export const cleanupApi={
  duplicates:()=>api('/cleanup/duplicates'),
  jobs:()=>api('/cleanup/jobs'),
  createJob:data=>api('/cleanup/jobs','POST',data||{}),
  runJob:id=>api('/cleanup/jobs/'+id+'/run','POST'),
  job:id=>api('/cleanup/jobs/'+id)
};
