/** Small dependency-free ZIP writer (stored entries) for browser-local preview packages. */
const table=Uint32Array.from({length:256},(_,i)=>{let c=i;for(let n=0;n<8;n++)c=(c&1)?0xedb88320^(c>>>1):c>>>1;return c>>>0;});
function crc32(bytes){let c=0xffffffff;for(const b of bytes)c=table[(c^b)&255]^(c>>>8);return(c^0xffffffff)>>>0;}
export function zipStored(files){
 const encoder=new TextEncoder(),parts=[],directory=[];let offset=0,directorySize=0;
 for(const [filename,text]of Object.entries(files)){
  const name=encoder.encode(filename),data=typeof text==='string'?encoder.encode(text):text,crc=crc32(data),header=new Uint8Array(30),view=new DataView(header.buffer);
  view.setUint32(0,0x04034b50,true);view.setUint16(4,20,true);view.setUint16(6,0x800,true);view.setUint32(14,crc,true);view.setUint32(18,data.length,true);view.setUint32(22,data.length,true);view.setUint16(26,name.length,true);parts.push(header,name,data);
  const entry=new Uint8Array(46),ev=new DataView(entry.buffer);ev.setUint32(0,0x02014b50,true);ev.setUint16(4,20,true);ev.setUint16(6,20,true);ev.setUint16(8,0x800,true);ev.setUint32(16,crc,true);ev.setUint32(20,data.length,true);ev.setUint32(24,data.length,true);ev.setUint16(28,name.length,true);ev.setUint32(42,offset,true);directory.push(entry,name);directorySize+=46+name.length;offset+=30+name.length+data.length;
 }
 const end=new Uint8Array(22),v=new DataView(end.buffer),count=Object.keys(files).length;v.setUint32(0,0x06054b50,true);v.setUint16(8,count,true);v.setUint16(10,count,true);v.setUint32(12,directorySize,true);v.setUint32(16,offset,true);
 return new Blob([...parts,...directory,end],{type:'application/zip'});
}
