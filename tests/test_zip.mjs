import {zipStored} from '../public/zip.js';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
const directory=await mkdtemp(path.join(tmpdir(),'q1-zip-'));
try{const filename=path.join(directory,'package.zip'),blob=zipStored({'manifest.json':'{"deployable":false}','中文.txt':'Q1 preview only\n'});await writeFile(filename,Buffer.from(await blob.arrayBuffer()));execFileSync('python3',['-c',"import zipfile,sys,json; z=zipfile.ZipFile(sys.argv[1]); assert z.testzip() is None; assert json.loads(z.read('manifest.json'))['deployable'] is False; assert z.read('中文.txt').decode() == 'Q1 preview only\\n'",filename]);console.log('Browser ZIP verified by Python zipfile: CRC, UTF-8 names and manifest contents.');}finally{await rm(directory,{recursive:true,force:true});}
