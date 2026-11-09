// Read-only, opt-in device regression probe; never loaded by the launcher.
// Uses local-auth's reflection helpers. All Unity reads run on its main thread.
(()=>{
  const unbox=api('object_unbox','pointer',['pointer']);
  const typeObject=api('type_get_object','pointer',['pointer']);
  const classType=api('class_get_type','pointer',['pointer']);
  const className=api('class_get_name','pointer',['pointer']);
  const findField=api('class_get_field_from_name','pointer',['pointer','pointer']);
  const fieldOffset=api('field_get_offset','int',['pointer']);
  const field=(o,key)=>o.add(fieldOffset(findField(getClass(o),Memory.allocUtf8String(key))));
  const find=new NativeFunction(mod.base.add(0xA454928),'pointer',['pointer','bool','pointer']);
  const read=(o,key)=>unbox(call(method(getClass(o),'get_'+key,0),o));
  function objects(ns,name){
    const found=find(typeObject(classType(klass(ns,name))),0,ptr(0)),result=[];
    for(let i=0;i<found.add(24).readU64().toNumber();i++)result.push(found.add(32+i*8).readPointer());
    return result;
  }
  const group=o=>({alpha:read(o,'alpha').readFloat(),interactable:!!read(o,'interactable').readU8(),blocksRaycasts:!!read(o,'blocksRaycasts').readU8()});
  let profileClicks=0,pending=[];
  Interceptor.attach(mod.base.add(0x7D1A4EC),{onEnter(){profileClicks++;}});
  function snapshot(){
    const hud=objects('Niantic.Holoholo.Map.UI','PlayerHudGuiController')[0];
    if(!hud)throw new Error('Trainer HUD not loaded');
    const research=objects('Niantic.Holoholo.Quests','QuestListStoryItemView').filter(view=>!view.add(0x280).readPointer().isNull()).map(view=>{
      const client=view.add(0x280).readPointer(),quest=field(client,'Quest').readPointer();
      const multi=call(method(getClass(quest),'get_MultiPart',0),quest);
      const children=field(multi,'SubQuests').readPointer();
      let claimed=0,total=read(children,'Count').readS32();
      for(let i=0;i<total;i++){
        const index=Memory.alloc(4);index.writeS32(i);
        const child=call(method(getClass(children),'get_Item',1),children,[index]);
        claimed+=field(child,'Status').readS32()===2?1:0;
      }
      const text=view.add(0x188).readPointer(),label=call(method(getClass(text),'get_text',0),text);
      return {claimed,total,taskViews:read(view.add(0x270).readPointer(),'Count').readS32(),completedLabel:label.add(20).readUtf16String(label.add(16).readS32())};
    });
    return {
      root:group(hud.add(0x40).readPointer()),trainer:group(hud.add(0x170).readPointer()),profileClicks,
      research,
      menuOpen:objects('Niantic.Holoholo.Map.UI','MainMenuGuiController').some(o=>o.add(0x330).readU8()!==0),
      visibleScreens:objects('Niantic.Holoholo.NUI','GuiController').filter(o=>read(o,'IsOpen').readU8()&&read(o,'IsVisible').readU8()).map(o=>className(getClass(o)).readUtf8String())
    };
  }
  Interceptor.attach(method(klass('UnityEngine','Canvas'),'SendWillRenderCanvases',0).readPointer(),{onEnter(){
    for(const job of pending.splice(0))try{job.resolve(snapshot());}catch(error){job.reject(error);}
  }});
  rpc.exports={snapshot(){return new Promise((resolve,reject)=>pending.push({resolve,reject}));}};
})();
