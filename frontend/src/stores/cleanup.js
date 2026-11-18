import {cleanupApi} from '../api/cleanup.js';
export const cleanupStore={duplicates:[],jobs:[],async refresh(){const [d,j]=await Promise.all([cleanupApi.duplicates(),cleanupApi.jobs()]);this.duplicates=d;this.jobs=j;return this;}};
