/* 父级标签用CSS隐藏框架时，document.hidden不会随之改变。 */
(() => {
  function createGate({delay=320,later=setTimeout,cancelLater=clearTimeout,frame=requestAnimationFrame,cancelFrame=cancelAnimationFrame,
                       dimensions,prepare,onState=()=>{},onReady=()=>{}}) {
    let wanted=null,active=false,generation=0,timer=null,animation=null,stopped=false;
    let state='boot',shows=0,hides=0,prepared=false;
    function cancel() {
      if(timer!=null)cancelLater(timer);
      if(animation!=null)cancelFrame(animation);
      timer=animation=null;
    }
    function change(next) {state=next;onState(next);}
    function measure(epoch,previous=null) {
      if(stopped || epoch!==generation || !wanted)return;
      const size=dimensions();
      if(!(size.width>0 && size.height>0)) {
        timer=later(()=>measure(epoch),100);
        return;
      }
      if(prepare && !prepared) {
        prepared=true;prepare();
        animation=frame(()=>measure(epoch));return;
      }
      if(previous && Math.abs(size.width-previous.width)<.5 && Math.abs(size.height-previous.height)<.5) {
        active=true;shows++;change('visible');onReady();return;
      }
      animation=frame(()=>measure(epoch,size));
    }
    function setVisible(next) {
      next=!!next;
      if(stopped || next===wanted)return;
      wanted=next;active=false;prepared=false;generation++;cancel();
      if(!next){hides++;change('hidden');return;}
      change('settling');const epoch=generation;
      timer=later(()=>{animation=frame(()=>measure(epoch));},delay);
    }
    function stop(){stopped=true;active=false;generation++;cancel();change('stopped');}
    return {setVisible,isActive:()=>active,stop,stats:()=>({state,shows,hides,generation})};
  }
  function restoreRootScale() {
    const root=document.documentElement;
    const before={layout:root.clientWidth,painted:root.getBoundingClientRect().width};
    if(!(before.layout>0) || Math.abs(before.painted-before.layout)<=2)return null;
    const position={left:scrollX,top:scrollY};
    // WebKitGTK 2.52在隐藏框架重算样式时，可能缓存额外的页面倍率。
    // 移除已生效的样式表会让文档根样式完整重算，刷新其pageScaleTransform。
    // 临时规则不改变视觉；保留现有样式表、页面节点和未保存的输入。
    const sheet=document.createElement('style');
    sheet.textContent=':root { --quota-layout-rebuild: 0; }';
    document.head.append(sheet);
    try {void root.offsetWidth;}
    finally {sheet.remove();}
    const painted=root.getBoundingClientRect().width;
    scrollTo(position.left,position.top);
    return {before,after:{layout:root.clientWidth,painted},resolved:Math.abs(painted-root.clientWidth)<=2};
  }
  function mount({onReady,onState}) {
    let intersecting=false,scaleRepairs=0,lastScaleRepair=null;
    const sentinel=document.createElement('div');
    sentinel.className='view-visibility-sentinel';sentinel.setAttribute('aria-hidden','true');
    document.body.append(sentinel);
    const gate=createGate({dimensions:()=>({width:document.documentElement.clientWidth,height:innerHeight}),
      prepare:()=>{const result=restoreRootScale();if(result){scaleRepairs++;lastScaleRepair=result;}},
      onReady,onState:state=>{document.documentElement.dataset.viewState=state;onState?.(state);}});
    const update=()=>gate.setVisible(intersecting && !document.hidden);
    const observer=new IntersectionObserver(entries=>{
      const item=entries[0];
      intersecting=!!item?.isIntersecting && item.intersectionRect.width>0 && item.intersectionRect.height>0;
      update();
    },{threshold:[0,.01]});
    observer.observe(sentinel);
    document.addEventListener('visibilitychange',update);
    addEventListener('pagehide',event=>{
      if(event.persisted){gate.setVisible(false);return;}
      observer.disconnect();gate.stop();sentinel.remove();
    });
    addEventListener('pageshow',event=>{if(event.persisted)update();});
    return {...gate,stats:()=>({...gate.stats(),scaleRepairs,lastScaleRepair})};
  }
  globalThis.quotaViewLifecycle={createGate,mount,restoreRootScale};
})();
