// One visibility owner for the trainer HUD's render AND interaction state.
// Kept independent of Unity so navigation sequences can be regression tested.
const LibreNlpHudVisibility=(()=>{
  const routeScreens=new Set(['NearbyRouteDetailsGuiController','NearbyRouteListGuiController',
    'NearbyRouteTopViewGuiController','NearbyRouteTutorialGuiController','NearbyRouteListSorterGuiController',
    'RouteCompletionGuiController','RouteRatingGuiController','RouteMedalGuiController',
    'RouteCreationActionsGuiController','RouteCreationAnchorPointSelectionGuiController',
    'RouteCreationPathEditGuiController','RouteSubmissionGuiController','RouteSubmissionModalGuiController',
    'RoutesCreationTutorialGuiController','RouteFinishVerifyWaypointGuiController']);
  function create(apply){
    let requested=true,guiVisible=false,menuOpen=false,last;
    const blocked=new Set();
    function sync(force=false){
      const visible=requested&&guiVisible&&!menuOpen&&blocked.size===0;
      if(force||visible!==last){apply(visible);last=visible;}
      return visible;
    }
    return {
      request(value){requested=!!value;return sync();},
      gui(value,overwritten=false){guiVisible=!!value;return sync(overwritten);},
      menu(value){menuOpen=!!value;return sync();},
      block(key,value){if(value)blocked.add(key);else blocked.delete(key);return sync();},
      visible(){return requested&&guiVisible&&!menuOpen&&blocked.size===0;}
    };
  }
  return {create,blocksRouteScreen:name=>routeScreens.has(name)};
})();

if(typeof module==='object'&&module.exports)module.exports=LibreNlpHudVisibility;

if(typeof Process!=='undefined')(()=>{
  const className=api('class_get_name','pointer',['pointer']);
  const unbox=api('object_unbox','pointer',['pointer']);
  const setVisible=new NativeFunction(mod.base.add(0x7D19F40),'void',['pointer','bool','pointer']);
  const setAlpha=new NativeFunction(mod.base.add(0xA779900),'void',['pointer','float','pointer']);
  const setInteractable=new NativeFunction(mod.base.add(0xA779AD4),'void',['pointer','bool','pointer']);
  const setBlocksRaycasts=new NativeFunction(mod.base.add(0xA779C94),'void',['pointer','bool','pointer']);
  let hud=ptr(0),menuOpen=false,internal=false,visibility;
  const screenBlocks=new Set();
  // Native Trainer battle states use a separate UI layer. Feed their existing
  // hooks into this same owner, including transitions before the HUD initializes.
  globalThis.LIBRE_HUD_BLOCK=(key,visible)=>{
    if(visible)screenBlocks.add(key);else screenBlocks.delete(key);
    if(visibility)visibility.block(key,visible);
  };
  const fullScreens=new Set(['BuddyGuiController','BuddyDetailsGuiController','BuddyHistoryGuiController',
    'BuddyInteractionGuiController','BuddyPlayControlGuiController','BuddyPlaySafetyGuiController',
    'BuddySwapGuiController','BuddySouvenirCollectionGuiController','BuddySouvenirDetailsGuiController',
    'BuddyGiftOpenGuiController','BuddyLevelUpGuiController','BadgeAwardGuiController','BadgeIncrementGuiController','LevelUpV3GuiController']);
  function capture(value){
    if(hud.equals(value)&&visibility)return;
    hud=value;
    visibility=LibreNlpHudVisibility.create(visible=>{
      // PlayerHudGuiController.SetVisible owns the SafeArea CanvasGroup and
      // sets alpha, interactable and blocksRaycasts together. No cover, no
      // avatar rebuild and no changes to profile-button callbacks.
      internal=true;
      try{
        setVisible(hud,visible?1:0,ptr(0));
        // Keep the outer group's children on the same policy. The radial menu
        // shares its GUI layer, so the ordinary layer system leaves this outer
        // group open during OpenMenu.
        const root=call(method(getClass(hud),'get_RootCanvasGroup',0),hud);
        setAlpha(root,visible?1:0,ptr(0));
        setInteractable(root,visible?1:0,ptr(0));
        setBlocksRaycasts(root,visible?1:0,ptr(0));
      }finally{internal=false;}
    });
    visibility.menu(menuOpen);
    for(const key of screenBlocks)visibility.block(key,true);
  }
  Interceptor.attach(mod.base.add(0x7D176D4),{ // PlayerHud.Initialize
    onEnter(args){this.hud=args[0];},
    onLeave(){
      capture(this.hud);
      const visible=call(method(getClass(hud),'get_IsVisible',0),hud);
      visibility.gui(!!unbox(visible).readU8());
    }
  });
  Interceptor.attach(mod.base.add(0x7D19F40),{ // independent HUD hide requests
    onEnter(args){
      if(internal)return;
      capture(args[0]);
      args[1]=ptr(visibility.request(args[1].toInt32()!==0)?1:0);
    }
  });
  Interceptor.attach(mod.base.add(0x8093FB8),{ // GuiController.CanvasGroupVisible
    onEnter(args){
      const name=args[0].isNull()?'':className(getClass(args[0])).readUtf8String();
      this.isHud=name==='PlayerHudGuiController';
      if(fullScreens.has(name)||LibreNlpHudVisibility.blocksRouteScreen(name)){
        this.blockKey=args[0].toString();this.blockVisible=args[1].toInt32()!==0;
      }
      if(this.isHud){this.hud=args[0];this.visible=args[1].toInt32()!==0;}
    },
    // The original setter just wrote the outer group. Reapply even if its
    // requested GUI visibility is unchanged while the radial menu is open.
    onLeave(){
      if(this.isHud){capture(this.hud);visibility.gui(this.visible,true);}
      if(this.blockKey){
        globalThis.LIBRE_HUD_BLOCK(this.blockKey,this.blockVisible);
      }
    }
  });
  function menu(value){menuOpen=value;if(visibility)visibility.menu(value);}
  Interceptor.attach(mod.base.add(0x7D11304),{ // MainMenu.OpenMenu (also drag)
    onEnter(args){this.menu=args[0];menu(true);},
    onLeave(){menu(this.menu.add(0x330).readU8()!==0);}
  });
  Interceptor.attach(mod.base.add(0x7D126F4),{ // MainMenu.CloseMenu
    onEnter(args){this.menu=args[0];},
    onLeave(){menu(this.menu.add(0x330).readU8()!==0);}
  });
  console.log('Trainer HUD navigation visibility active');
})();
