import{d as ee,aq as Te,aQ as He,aB as Re,ac as f,aI as Ee,aa as N,j as y,c as $,v as We,au as Ie,n as W,as as _e,aR as ce,ah as Fe,az as m,ab as d,ay as ae,ad as ue,f as Z,w as De,b as ie,ae as M,ak as Le,i as O,aj as Ke,av as je,F as se,aS as Me,ag as t,aT as A}from"./index-B7_MPLxr.js";import{n as k,h as Oe,m as Ae,l as Ge,r as le,a as C,u as Ne,k as Ve,e as qe,b as Qe,g as Xe,o as Ye,f as Ue,I as Je,L as Ze}from"./browser-BiOuxToN.js";function de(e){return e.replace(/#|\(|\)|,|\s|\./g,"_")}var eo=ee({name:"FadeInExpandTransition",props:{appear:Boolean,group:Boolean,mode:String,onLeave:Function,onAfterLeave:Function,onAfterEnter:Function,width:Boolean,reverse:Boolean},setup(e,{slots:h}){function p(r){e.width?r.style.maxWidth=`${r.offsetWidth}px`:r.style.maxHeight=`${r.offsetHeight}px`,r.offsetWidth}function c(r){e.width?r.style.maxWidth="0":r.style.maxHeight="0",r.offsetWidth;const{onLeave:b}=e;b&&b()}function x(r){e.width?r.style.maxWidth="":r.style.maxHeight="";const{onAfterLeave:b}=e;b&&b()}function V(r){if(r.style.transition="none",e.width){const b=r.offsetWidth;r.style.maxWidth="0",r.offsetWidth,r.style.transition="",r.style.maxWidth=`${b}px`}else if(e.reverse)r.style.maxHeight=`${r.offsetHeight}px`,r.offsetHeight,r.style.transition="",r.style.maxHeight="0";else{const b=r.offsetHeight;r.style.maxHeight="0",r.offsetWidth,r.style.transition="",r.style.maxHeight=`${b}px`}r.offsetWidth}function I(r){e.width?r.style.maxWidth="":e.reverse||(r.style.maxHeight=""),e.onAfterEnter?.()}return()=>{const{group:r,width:b,appear:q,mode:R}=e,E=r?He:Re,_={name:b?"fade-in-width-expand-transition":"fade-in-height-expand-transition",appear:q,onEnter:V,onAfterEnter:I,onBeforeLeave:p,onLeave:c,onAfterLeave:x};return r||(_.mode=R),Te(E,_,h)}}});const{cubicBezierEaseInOut:z}=Ee;function oo({duration:e=".2s",delay:h=".1s"}={}){return[f("&.fade-in-width-expand-transition-leave-from, &.fade-in-width-expand-transition-enter-to",{opacity:1}),f("&.fade-in-width-expand-transition-leave-to, &.fade-in-width-expand-transition-enter-from",`
 opacity: 0!important;
 margin-left: 0!important;
 margin-right: 0!important;
 `),f("&.fade-in-width-expand-transition-leave-active",`
 overflow: hidden;
 transition:
 opacity ${e} ${z},
 max-width ${e} ${z} ${h},
 margin-left ${e} ${z} ${h},
 margin-right ${e} ${z} ${h};
 `),f("&.fade-in-width-expand-transition-enter-active",`
 overflow: hidden;
 transition:
 opacity ${e} ${z} ${h},
 max-width ${e} ${z},
 margin-left ${e} ${z},
 margin-right ${e} ${z};
 `)]}var ro=N("base-wave",`
 position: absolute;
 left: 0;
 right: 0;
 top: 0;
 bottom: 0;
 border-radius: inherit;
`),to=ee({name:"BaseWave",props:{clsPrefix:{type:String,required:!0}},setup(e){Oe("-base-wave",ro,_e(e,"clsPrefix"));const h=W(null),p=W(!1);let c=null;return We(()=>{c!==null&&window.clearTimeout(c)}),{active:p,selfRef:h,play(){c!==null&&(window.clearTimeout(c),p.value=!1,c=null),Ie(()=>{h.value?.offsetHeight,p.value=!0,c=window.setTimeout(()=>{p.value=!1,c=null},1e3)})}}},render(){const{clsPrefix:e}=this;return y(),$("div",{ref:"selfRef","aria-hidden":!0,class:k([`${e}-base-wave`,this.active&&`${e}-base-wave--active`])},null,2)}});function P(e){return ce(e,[255,255,255,.16])}function G(e){return ce(e,[0,0,0,.12])}const no=Fe("n-button-group");var ao=f([N("button",`
 margin: 0;
 font-weight: var(--n-font-weight);
 line-height: 1;
 font-family: inherit;
 padding: var(--n-padding);
 height: var(--n-height);
 font-size: var(--n-font-size);
 border-radius: var(--n-border-radius);
 color: var(--n-text-color);
 background-color: var(--n-color);
 width: var(--n-width);
 white-space: nowrap;
 outline: none;
 position: relative;
 z-index: auto;
 border: none;
 display: inline-flex;
 flex-wrap: nowrap;
 flex-shrink: 0;
 align-items: center;
 justify-content: center;
 user-select: none;
 -webkit-user-select: none;
 text-align: center;
 cursor: pointer;
 text-decoration: none;
 transition:
 color .3s var(--n-bezier),
 background-color .3s var(--n-bezier),
 opacity .3s var(--n-bezier),
 border-color .3s var(--n-bezier);
 `,[m("color",[d("border",{borderColor:"var(--n-border-color)"}),m("disabled",[d("border",{borderColor:"var(--n-border-color-disabled)"})]),ae("disabled",[f("&:focus",[d("state-border",{borderColor:"var(--n-border-color-focus)"})]),f("&:hover",[d("state-border",{borderColor:"var(--n-border-color-hover)"})]),f("&:active",[d("state-border",{borderColor:"var(--n-border-color-pressed)"})]),m("pressed",[d("state-border",{borderColor:"var(--n-border-color-pressed)"})])])]),m("disabled",{backgroundColor:"var(--n-color-disabled)",color:"var(--n-text-color-disabled)"},[d("border",{border:"var(--n-border-disabled)"})]),ae("disabled",[f("&:focus",{backgroundColor:"var(--n-color-focus)",color:"var(--n-text-color-focus)"},[d("state-border",{border:"var(--n-border-focus)"})]),f("&:hover",{backgroundColor:"var(--n-color-hover)",color:"var(--n-text-color-hover)"},[d("state-border",{border:"var(--n-border-hover)"})]),f("&:active",{backgroundColor:"var(--n-color-pressed)",color:"var(--n-text-color-pressed)"},[d("state-border",{border:"var(--n-border-pressed)"})]),m("pressed",{backgroundColor:"var(--n-color-pressed)",color:"var(--n-text-color-pressed)"},[d("state-border",{border:"var(--n-border-pressed)"})])]),m("loading","cursor: wait;"),N("base-wave",`
 pointer-events: none;
 top: 0;
 right: 0;
 bottom: 0;
 left: 0;
 animation-iteration-count: 1;
 animation-duration: var(--n-ripple-duration);
 animation-timing-function: var(--n-bezier-ease-out), var(--n-bezier-ease-out);
 `,[m("active",{zIndex:1,animationName:"button-wave-spread, button-wave-opacity"})]),Ae&&"MozBoxSizing"in document.createElement("div").style?f("&::moz-focus-inner",{border:0}):null,d("border, state-border",`
 position: absolute;
 left: 0;
 top: 0;
 right: 0;
 bottom: 0;
 border-radius: inherit;
 transition: border-color .3s var(--n-bezier);
 pointer-events: none;
 `),d("border",`
 border: var(--n-border);
 `),d("state-border",`
 border: var(--n-border);
 border-color: #0000;
 z-index: 1;
 `),d("icon",`
 margin: var(--n-icon-margin);
 margin-left: 0;
 height: var(--n-icon-size);
 width: var(--n-icon-size);
 max-width: var(--n-icon-size);
 font-size: var(--n-icon-size);
 position: relative;
 flex-shrink: 0;
 `,[N("icon-slot",`
 height: var(--n-icon-size);
 width: var(--n-icon-size);
 position: absolute;
 left: 0;
 top: 50%;
 transform: translateY(-50%);
 display: flex;
 align-items: center;
 justify-content: center;
 `,[Ge({top:"50%",originalTransform:"translateY(-50%)"})]),oo()]),d("content",`
 display: flex;
 align-items: center;
 flex-wrap: nowrap;
 min-width: 0;
 `,[f("~",[d("icon",{margin:"var(--n-icon-margin)",marginRight:0})])]),m("block",`
 display: flex;
 width: 100%;
 `),m("dashed",[d("border, state-border",{borderStyle:"dashed !important"})]),m("disabled",{cursor:"not-allowed",opacity:"var(--n-opacity-disabled)"})]),f("@keyframes button-wave-spread",{from:{boxShadow:"0 0 0.5px 0 var(--n-ripple-color)"},to:{boxShadow:"0 0 0.5px 4.5px var(--n-ripple-color)"}}),f("@keyframes button-wave-opacity",{from:{opacity:"var(--n-wave-opacity)"},to:{opacity:0}})]);const io={...ue.props,color:String,textColor:String,text:Boolean,block:Boolean,loading:Boolean,disabled:Boolean,circle:Boolean,size:String,ghost:Boolean,round:Boolean,secondary:Boolean,tertiary:Boolean,quaternary:Boolean,strong:Boolean,focusable:{type:Boolean,default:!0},keyboard:{type:Boolean,default:!0},tag:{type:String,default:"button"},type:{type:String,default:"default"},dashed:Boolean,renderIcon:Function,iconPlacement:{type:String,default:"left"},attrType:{type:String,default:"button"},bordered:{type:Boolean,default:!0},onClick:[Function,Array],nativeFocusBehavior:{type:Boolean,default:!Ye},spinProps:Object},so=ee({name:"Button",props:io,slots:Object,setup(e){const h=W(null),p=W(null),c=W(!1),x=Le(()=>!e.quaternary&&!e.tertiary&&!e.secondary&&!e.text&&(!e.color||e.ghost||e.dashed)&&e.bordered),V=Ke(no,{}),{inlineThemeDisabled:I,mergedClsPrefixRef:r,mergedRtlRef:b,mergedComponentPropsRef:q}=Ne(e),{mergedSizeRef:R}=Ve({},{defaultSize:"medium",mergedSize:n=>{const{size:v}=e;if(v)return v;const{size:o}=V;if(o)return o;const{mergedSize:T}=n||{};if(T)return T.value;const H=q?.value?.Button?.size;return H||"medium"}}),E=O(()=>e.focusable&&!e.disabled),_=n=>{E.value||n.preventDefault(),!e.nativeFocusBehavior&&(n.preventDefault(),!e.disabled&&E.value&&h.value?.focus({preventScroll:!0}))},fe=n=>{if(!e.disabled&&!e.loading){const{onClick:v}=e;v&&Xe(v,n),e.text||p.value?.play()}},he=n=>{switch(n.key){case"Enter":if(!e.keyboard)return;c.value=!1}},be=n=>{switch(n.key){case"Enter":if(!e.keyboard||e.loading){n.preventDefault();return}c.value=!0}},ve=()=>{c.value=!1},pe=ue("Button","-button",ao,Me,e,r),ye=qe("Button",b,r),oe=O(()=>{const{common:{cubicBezierEaseInOut:n,cubicBezierEaseOut:v},self:o}=pe.value,{rippleDuration:T,opacityDisabled:H,fontWeight:F,fontWeightStrong:Q}=o,g=R.value,{dashed:D,type:B,ghost:X,text:w,color:i,round:L,circle:Y,textColor:S,secondary:me,tertiary:te,quaternary:xe,strong:ge}=e,we={"--n-font-weight":ge?Q:F};let s={"--n-color":"initial","--n-color-hover":"initial","--n-color-pressed":"initial","--n-color-focus":"initial","--n-color-disabled":"initial","--n-ripple-color":"initial","--n-text-color":"initial","--n-text-color-hover":"initial","--n-text-color-pressed":"initial","--n-text-color-focus":"initial","--n-text-color-disabled":"initial"};const K=B==="tertiary",ne=B==="default",a=K?"default":B;if(w){const l=S||i;s={"--n-color":"#0000","--n-color-hover":"#0000","--n-color-pressed":"#0000","--n-color-focus":"#0000","--n-color-disabled":"#0000","--n-ripple-color":"#0000","--n-text-color":l||o[t("textColorText",a)],"--n-text-color-hover":l?P(l):o[t("textColorTextHover",a)],"--n-text-color-pressed":l?G(l):o[t("textColorTextPressed",a)],"--n-text-color-focus":l?P(l):o[t("textColorTextHover",a)],"--n-text-color-disabled":l||o[t("textColorTextDisabled",a)]}}else if(X||D){const l=S||i;s={"--n-color":"#0000","--n-color-hover":"#0000","--n-color-pressed":"#0000","--n-color-focus":"#0000","--n-color-disabled":"#0000","--n-ripple-color":i||o[t("rippleColor",a)],"--n-text-color":l||o[t("textColorGhost",a)],"--n-text-color-hover":l?P(l):o[t("textColorGhostHover",a)],"--n-text-color-pressed":l?G(l):o[t("textColorGhostPressed",a)],"--n-text-color-focus":l?P(l):o[t("textColorGhostHover",a)],"--n-text-color-disabled":l||o[t("textColorGhostDisabled",a)]}}else if(me){const l=ne?o.textColor:K?o.textColorTertiary:o[t("color",a)],u=i||l,j=B!=="default"&&B!=="tertiary";s={"--n-color":j?A(u,{alpha:Number(o.colorOpacitySecondary)}):o.colorSecondary,"--n-color-hover":j?A(u,{alpha:Number(o.colorOpacitySecondaryHover)}):o.colorSecondaryHover,"--n-color-pressed":j?A(u,{alpha:Number(o.colorOpacitySecondaryPressed)}):o.colorSecondaryPressed,"--n-color-focus":j?A(u,{alpha:Number(o.colorOpacitySecondaryHover)}):o.colorSecondaryHover,"--n-color-disabled":o.colorSecondary,"--n-ripple-color":"#0000","--n-text-color":u,"--n-text-color-hover":u,"--n-text-color-pressed":u,"--n-text-color-focus":u,"--n-text-color-disabled":u}}else if(te||xe){const l=ne?o.textColor:K?o.textColorTertiary:o[t("color",a)],u=i||l;te?(s["--n-color"]=o.colorTertiary,s["--n-color-hover"]=o.colorTertiaryHover,s["--n-color-pressed"]=o.colorTertiaryPressed,s["--n-color-focus"]=o.colorSecondaryHover,s["--n-color-disabled"]=o.colorTertiary):(s["--n-color"]=o.colorQuaternary,s["--n-color-hover"]=o.colorQuaternaryHover,s["--n-color-pressed"]=o.colorQuaternaryPressed,s["--n-color-focus"]=o.colorQuaternaryHover,s["--n-color-disabled"]=o.colorQuaternary),s["--n-ripple-color"]="#0000",s["--n-text-color"]=u,s["--n-text-color-hover"]=u,s["--n-text-color-pressed"]=u,s["--n-text-color-focus"]=u,s["--n-text-color-disabled"]=u}else s={"--n-color":i||o[t("color",a)],"--n-color-hover":i?P(i):o[t("colorHover",a)],"--n-color-pressed":i?G(i):o[t("colorPressed",a)],"--n-color-focus":i?P(i):o[t("colorFocus",a)],"--n-color-disabled":i||o[t("colorDisabled",a)],"--n-ripple-color":i||o[t("rippleColor",a)],"--n-text-color":S||(i?o.textColorPrimary:K?o.textColorTertiary:o[t("textColor",a)]),"--n-text-color-hover":S||(i?o.textColorHoverPrimary:o[t("textColorHover",a)]),"--n-text-color-pressed":S||(i?o.textColorPressedPrimary:o[t("textColorPressed",a)]),"--n-text-color-focus":S||(i?o.textColorFocusPrimary:o[t("textColorFocus",a)]),"--n-text-color-disabled":S||(i?o.textColorDisabledPrimary:o[t("textColorDisabled",a)])};let U={"--n-border":"initial","--n-border-hover":"initial","--n-border-pressed":"initial","--n-border-focus":"initial","--n-border-disabled":"initial"};w?U={"--n-border":"none","--n-border-hover":"none","--n-border-pressed":"none","--n-border-focus":"none","--n-border-disabled":"none"}:U={"--n-border":o[t("border",a)],"--n-border-hover":o[t("borderHover",a)],"--n-border-pressed":o[t("borderPressed",a)],"--n-border-focus":o[t("borderFocus",a)],"--n-border-disabled":o[t("borderDisabled",a)]};const{[t("height",g)]:J,[t("fontSize",g)]:Ce,[t("padding",g)]:ze,[t("paddingRound",g)]:$e,[t("iconSize",g)]:Be,[t("borderRadius",g)]:Se,[t("iconMargin",g)]:Pe,waveOpacity:ke}=o;return{"--n-bezier":n,"--n-bezier-ease-out":v,"--n-ripple-duration":T,"--n-opacity-disabled":H,"--n-wave-opacity":ke,...we,...s,...U,...{"--n-width":Y&&!w?J:"initial","--n-height":w?"initial":J,"--n-font-size":Ce,"--n-padding":Y||w?"initial":L?$e:ze,"--n-icon-size":Be,"--n-icon-margin":Pe,"--n-border-radius":w?"initial":Y||L?J:Se}}}),re=I?Qe("button",O(()=>{let n="";const{dashed:v,type:o,ghost:T,text:H,color:F,round:Q,circle:g,textColor:D,secondary:B,tertiary:X,quaternary:w,strong:i}=e;v&&(n+="a"),T&&(n+="b"),H&&(n+="c"),Q&&(n+="d"),g&&(n+="e"),B&&(n+="f"),X&&(n+="g"),w&&(n+="h"),i&&(n+="i"),F&&(n+=`j${de(F)}`),D&&(n+=`k${de(D)}`);const{value:L}=R;return n+=`l${L[0]}`,n+=`m${o[0]}`,n}),oe,e):void 0;return{selfElRef:h,waveElRef:p,mergedClsPrefix:r,mergedFocusable:E,mergedSize:R,showBorder:x,enterPressed:c,rtlEnabled:ye,handleMousedown:_,handleKeydown:be,handleBlur:ve,handleKeyup:he,handleClick:fe,customColorCssVars:O(()=>{const{color:n}=e;if(!n)return null;const v=P(n);return{"--n-border-color":n,"--n-border-color-hover":v,"--n-border-color-pressed":G(n),"--n-border-color-focus":v,"--n-border-color-disabled":n}}),cssVars:I?void 0:oe,themeClass:re?.themeClass,onRender:re?.onRender}},render(){const{mergedClsPrefix:e,tag:h,onRender:p}=this;p?.();const c=le(this.$slots.default,x=>x&&(y(),$("span",{class:k(`${e}-button__content`)},[C(()=>x)],2)));return y(),Z(h,{ref:"selfElRef",class:k([this.themeClass,`${e}-button`,`${e}-button--${this.type}-type`,`${e}-button--${this.mergedSize}-type`,this.rtlEnabled&&`${e}-button--rtl`,this.disabled&&`${e}-button--disabled`,this.block&&`${e}-button--block`,this.enterPressed&&`${e}-button--pressed`,!this.text&&this.dashed&&`${e}-button--dashed`,this.color&&`${e}-button--color`,this.secondary&&`${e}-button--secondary`,this.loading&&`${e}-button--loading`,this.ghost&&`${e}-button--ghost`]),tabindex:this.mergedFocusable?0:-1,type:this.attrType,style:M(this.cssVars),disabled:this.disabled,onClick:this.handleClick,onBlur:this.handleBlur,onMousedown:this.handleMousedown,onKeyup:this.handleKeyup,onKeydown:this.handleKeydown},{default:De(()=>[C(()=>this.iconPlacement==="right"&&c),ie(eo,{width:!0},{default:()=>le(this.$slots.icon,x=>(this.loading||this.renderIcon||x)&&(y(),$("span",{class:k(`${e}-button__icon`),style:M({margin:Ue(this.$slots.default)?"0":""})},[ie(Je,null,{default:()=>this.loading?(y(),Z(Ze,je({clsPrefix:e,key:"loading",class:`${e}-icon-slot`,strokeWidth:20},this.spinProps),null,16,["clsPrefix","class"])):(y(),$("div",{key:"icon",class:k(`${e}-icon-slot`),role:"none"},[this.renderIcon?(y(),$(se,{key:0},[C(()=>this.renderIcon())],64)):(y(),$(se,{key:1},[C(()=>x)],64))],2))},1024)],6)))},1024),C(()=>this.iconPlacement==="left"&&c),this.text?C(()=>null):(y(),Z(to,{key:0,ref:"waveElRef",clsPrefix:e},null,8,["clsPrefix"])),this.showBorder?(y(),$("div",{key:2,"aria-hidden":!0,class:k(`${e}-button__border`),style:M(this.customColorCssVars)},null,6)):C(()=>null),this.showBorder?(y(),$("div",{key:4,"aria-hidden":!0,class:k(`${e}-button__state-border`),style:M(this.customColorCssVars)},null,6)):C(()=>null)]),_:2},1032,["class","tabindex","type","style","disabled","onClick","onBlur","onMousedown","onKeyup","onKeydown"])}}),fo=so;export{so as B,fo as X,de as c};
