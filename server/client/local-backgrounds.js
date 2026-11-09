// One project-owned original texture, loaded through the native Addressables
// bundle providers. The server's opt-in card definitions control its use.
(()=>{
  let installed=false;
  const str=s=>stringNew(Memory.allocUtf8String(s));
  const type=k=>api('type_get_object','pointer',['pointer'])(api('class_get_type','pointer',['pointer'])(k));
  const hook=Interceptor.attach(method(klass('UnityEngine','Canvas'),'SendWillRenderCanvases',0).readPointer(),{onEnter(){
    if(installed)return;installed=true;
    try{
      const locClass=klass('UnityEngine.ResourceManagement.ResourceLocations','ResourceLocationBase');
      const iface=klass('UnityEngine.ResourceManagement.ResourceLocations','IResourceLocation');
      const provider='UnityEngine.ResourceManagement.ResourceProviders.';
      const bundleUrl=str('http://127.0.0.1:8080/assets/dittobuffer-original-background.bundle');
      const bundle=objectNew(locClass),empty=arrayNew(iface,0);
      call(method(locClass,'.ctor',5),bundle,[bundleUrl,bundleUrl,str(provider+'AssetBundleProvider'),
        type(klass('UnityEngine.ResourceManagement.ResourceProviders','IAssetBundleResource')),empty]);
      const options=objectNew(klass('UnityEngine.ResourceManagement.ResourceProviders','AssetBundleRequestOptions'));
      call(method(getClass(options),'.ctor',0),options);
      call(method(getClass(options),'set_BundleName',1),options,[str('dittobuffer-original-background')]);
      call(method(locClass,'set_Data',1),bundle,[options]);
      const deps=arrayNew(iface,1);
      api('gc_wbarrier_set_field','void',['pointer','pointer','pointer'])(deps,deps.add(32),bundle);
      const asset=objectNew(locClass),key=str('http://127.0.0.1:8080/assets/dittobuffer-stop.png');
      call(method(locClass,'.ctor',5),asset,[key,str('assets/libre/background.png'),str(provider+'BundledAssetProvider'),type(klass('UnityEngine','Texture2D')),deps]);
      const map=objectNew(klass('UnityEngine.AddressableAssets.ResourceLocators','ResourceLocationMap'));
      // Exact overloads from supplied 0.429.2 metadata.
      new NativeFunction(mod.base.add(0x9EEB5B0),'void',['pointer','pointer','int','pointer'])(map,str('Libre original artwork'),1,ptr(0));
      new NativeFunction(mod.base.add(0x9EEDF78),'void',Array(4).fill('pointer'))(map,key,asset,ptr(0));
      const addressables=klass('UnityEngine.AddressableAssets','Addressables');
      call(method(addressables,'AddResourceLocator',3),ptr(0),[map,ptr(0),ptr(0)]);
      console.log('Original local background Addressables resource registered');
    }catch(e){console.log('Local background resource registration failed: '+e.message);}
    hook.detach();
  }});
})();
