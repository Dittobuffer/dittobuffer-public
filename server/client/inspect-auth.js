// Read-only metadata inspection, restricted to the isolated test application by launcher.
const module = Process.getModuleByName('libil2cpp.so');
const api=(name,result,args)=>new NativeFunction(module.getExportByName('il2cpp_'+name),result,args);
const domain=api('domain_get','pointer',[])();
api('thread_attach','pointer',['pointer'])(domain);
const ret=api('method_get_return_type','pointer',['pointer']);
const klassFromType=api('class_from_type','pointer',['pointer']);
const methods=api('class_get_methods','pointer',['pointer','pointer']);
const methodName=api('method_get_name','pointer',['pointer']);
const paramCount=api('method_get_param_count','uint',['pointer']);
const count=Memory.alloc(Process.pointerSize);count.writeU64(0);
const assemblies=api('domain_get_assemblies','pointer',['pointer','pointer'])(domain,count);
const image=api('assembly_get_image','pointer',['pointer']);
const fromName=api('class_from_name','pointer',['pointer','pointer','pointer']);
const ns=Memory.allocUtf8String('Niantic.Platform.Auth');
const name=Memory.allocUtf8String('GoogleAuthSource');
for(let i=0;i<count.readU64().toNumber();i++){
    const k=fromName(image(assemblies.add(i*Process.pointerSize).readPointer()),ns,name);
    if(k.isNull())continue;
    const method=api('class_get_method_from_name','pointer',['pointer','pointer','int'])(k,Memory.allocUtf8String('Login'),0);
    const future=klassFromType(ret(method));
    const it=Memory.alloc(Process.pointerSize);it.writePointer(ptr(0));
    let m;
    while(!(m=methods(future,it)).isNull())console.log(methodName(m).readUtf8String()+' '+paramCount(m)+' @ '+m.readPointer().sub(module.base));
}
