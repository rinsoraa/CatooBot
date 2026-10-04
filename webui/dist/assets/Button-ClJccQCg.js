import{d as ee,ar as Te,aR as He,aC as Re,ad as f,aJ as Ee,ab as j,k as x,c as $,x as We,av as _e,p as W,at as Fe,aS as ce,ai as Ie,aA as y,ac as d,az as ae,ae as ue,f as Z,w as De,b as ie,af as M,al as Le,i as O,ak as Ke,aw as Ae,F as se,aT as Me,ah as t,aU as G}from"./index-C1imMpqx.js";import{n as P,h as Oe,m as Ge,l as Ne,r as le,a as C,u as je,k as Ve,e as qe,b as Qe,g as Ue,o as Xe,f as Ye,I as Je,L as Ze}from"./browser-B0BPpWDD.js";function de(e){return e.replace(/#|\(|\)|,|\s|\./g,"_")}var eo=ee({name:"FadeInExpandTransition",props:{appear:Boolean,group:Boolean,mode:String,onLeave:Function,onAfterLeave:Function,onAfterEnter:Function,width:Boolean,reverse:Boolean},setup(e,{slots:h}){function p(r){e.width?r.style.maxWidth=`${r.offsetWidth}px`:r.style.maxHeight=`${r.offsetHeight}px`,r.offsetWidth}function c(r){e.width?r.style.maxWidth="0":r.style.maxHeight="0",r.offsetWidth;const{onLeave:b}=e;b&&b()}function m(r){e.width?r.style.maxWidth="":r.style.maxHeight="";const{onAfterLeave:b}=e;b&&b()}function V(r){if(r.style.transition="none",e.width){const b=r.offsetWidth;r.style.maxWidth="0",r.offsetWidth,r.style.transition="",r.style.maxWidth=`${b}px`}else if(e.reverse)r.style.maxHeight=`${r.offsetHeight}px`,r.offsetHeight,r.style.transition="",r.style.maxHeight="0";else{const b=r.offsetHeight;r.style.maxHeight="0",r.offsetWidth,r.style.transition="",r.style.maxHeight=`${b}px`}r.offsetWidth}function _(r){e.width?r.style.maxWidth="":e.reverse||(r.style.maxHeight=""),e.onAfterEnter?.()}return()=>{const{group:r,width:b,appear:q,mode:R}=e,E=r?He:Re,F={name:b?"fade-in-width-expand-transition":"fade-in-height-expand-transition",appear:q,onEnter:V,onAfterEnter:_,onBeforeLeave:p,onLeave:c,onAfterLeave:m};return r||(F.mode=R),Te(E,F,h)}}});const{cubicBezierEaseInOut:z}=Ee;function oo({duration:e=".2s",delay:h=".1s"}={}){return[f("&.fade-in-width-expand-transition-leave-from, &.fade-in-width-expand-transition-enter-to",{opacity:1}),f("&.fade-in-width-expand-transition-leave-to, &.fade-in-width-expand-transition-enter-from",`
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
 `)]}var ro=j("base-wave",`
 position: absolute;
 left: 0;
 right: 0;
 top: 0;
 bottom: 0;
 border-radius: inherit;
`),to=ee({name:"BaseWave",props:{clsPrefix:{type:String,required:!0}},setup(e){Oe("-base-wave",ro,Fe(e,"clsPrefix"));const h=W(null),p=W(!1);let c=null;return We(()=>{c!==null&&window.clearTimeout(c)}),{active:p,selfRef:h,play(){c!==null&&(window.clearTimeout(c),p.value=!1,c=null),_e(()=>{h.value?.offsetHeight,p.value=!0,c=window.setTimeout(()=>{p.value=!1,c=null},1e3)})}}},render(){const{clsPrefix:e}=this;return x(),$("div",{ref:"selfRef","aria-hidden":!0,class:P([`${e}-base-wave`,this.active&&`${e}-base-wave--active`])},null,2)}});function k(e){return ce(e,[255,255,255,.16])}function N(e){return ce(e,[0,0,0,.12])}const no=Ie("n-button-group");var ao=f([j("button",`
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
 `,[y("color",[d("border",{borderColor:"var(--n-border-color)"}),y("disabled",[d("border",{borderColor:"var(--n-border-color-disabled)"})]),ae("disabled",[f("&:focus",[d("state-border",{borderColor:"var(--n-border-color-focus)"})]),f("&:hover",[d("state-border",{borderColor:"var(--n-border-color-hover)"})]),f("&:active",[d("state-border",{borderColor:"var(--n-border-color-pressed)"})]),y("pressed",[d("state-border",{borderColor:"var(--n-border-color-pressed)"})])])]),y("disabled",{backgroundColor:"var(--n-color-disabled)",color:"var(--n-text-color-disabled)"},[d("border",{border:"var(--n-border-disabled)"})]),ae("disabled",[f("&:focus",{backgroundColor:"var(--n-color-focus)",color:"var(--n-text-color-focus)"},[d("state-border",{border:"var(--n-border-focus)"})]),f("&:hover",{backgroundColor:"var(--n-color-hover)",color:"var(--n-text-color-hover)"},[d("state-border",{border:"var(--n-border-hover)"})]),f("&:active",{backgroundColor:"var(--n-color-pressed)",color:"var(--n-text-color-pressed)"},[d("state-border",{border:"var(--n-border-pressed)"})]),y("pressed",{backgroundColor:"var(--n-color-pressed)",color:"var(--n-text-color-pressed)"},[d("state-border",{border:"var(--n-border-pressed)"})])]),y("loading","cursor: wait;"),j("base-wave",`
 pointer-events: none;
 top: 0;
 right: 0;
 bottom: 0;
 left: 0;
 animation-iteration-count: 1;
 animation-duration: var(--n-ripple-duration);
 animation-timing-function: var(--n-bezier-ease-out), var(--n-bezier-ease-out);
 `,[y("active",{zIndex:1,animationName:"button-wave-spread, button-wave-opacity"})]),Ge&&"MozBoxSizing"in document.createElement("div").style?f("&::moz-focus-inner",{border:0}):null,d("border, state-border",`
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
 `,[j("icon-slot",`
 height: var(--n-icon-size);
 width: var(--n-icon-size);
 position: absolute;
 left: 0;
 top: 50%;
 transform: translateY(-50%);
 display: flex;
 align-items: center;
 justify-content: center;
 `,[Ne({top:"50%",originalTransform:"translateY(-50%)"})]),oo()]),d("content",`
 display: flex;
 align-items: center;
 flex-wrap: nowrap;
 min-width: 0;
 `,[f("~",[d("icon",{margin:"var(--n-icon-margin)",marginRight:0})])]),y("block",`
 display: flex;
 width: 100%;
 `),y("dashed",[d("border, state-border",{borderStyle:"dashed !important"})]),y("disabled",{cursor:"not-allowed",opacity:"var(--n-opacity-disabled)"})]),f("@keyframes button-wave-spread",{from:{boxShadow:"0 0 0.5px 0 var(--n-ripple-color)"},to:{boxShadow:"0 0 0.5px 4.5px var(--n-ripple-color)"}}),f("@keyframes button-wave-opacity",{from:{opacity:"var(--n-wave-opacity)"},to:{opacity:0}})]);const io={...ue.props,color:String,textColor:String,text:Boolean,block:Boolean,loading:Boolean,disabled:Boolean,circle:Boolean,size:String,ghost:Boolean,round:Boolean,secondary:Boolean,tertiary:Boolean,quaternary:Boolean,strong:Boolean,focusable:{type:Boolean,default:!0},keyboard:{type:Boolean,default:!0},tag:{type:String,default:"button"},type:{type:String,default:"default"},dashed:Boolean,renderIcon:Function,iconPlacement:{type:String,default:"left"},attrType:{type:String,default:"button"},bordered:{type:Boolean,default:!0},onClick:[Function,Array],nativeFocusBehavior:{type:Boolean,default:!Xe},spinProps:Object},so=ee({name:"Button",props:io,slots:Object,setup(e){const h=W(null),p=W(null),c=W(!1),m=Le(()=>!e.quaternary&&!e.tertiary&&!e.secondary&&!e.text&&(!e.color||e.ghost||e.dashed)&&e.bordered),V=Ke(no,{}),{inlineThemeDisabled:_,mergedClsPrefixRef:r,mergedRtlRef:b,mergedComponentPropsRef:q}=je(e),{mergedSizeRef:R}=Ve({},{defaultSize:"medium",mergedSize:n=>{const{size:v}=e;if(v)return v;const{size:o}=V;if(o)return o;const{mergedSize:T}=n||{};if(T)return T.value;const H=q?.value?.Button?.size;return H||"medium"}}),E=O(()=>e.focusable&&!e.disabled),F=n=>{E.value||n.preventDefault(),!e.nativeFocusBehavior&&(n.preventDefault(),!e.disabled&&E.value&&h.value?.focus({preventScroll:!0}))},fe=n=>{if(!e.disabled&&!e.loading){const{onClick:v}=e;v&&Ue(v,n),e.text||p.value?.play()}},he=n=>{switch(n.key){case"Enter":if(!e.keyboard)return;c.value=!1}},be=n=>{switch(n.key){case"Enter":if(!e.keyboard||e.loading){n.preventDefault();return}c.value=!0}},ve=()=>{c.value=!1},pe=ue("Button","-button",ao,Me,e,r),xe=qe("Button",b,r),oe=O(()=>{const{common:{cubicBezierEaseInOut:n,cubicBezierEaseOut:v},self:o}=pe.value,{rippleDuration:T,opacityDisabled:H,fontWeight:I,fontWeightStrong:Q}=o,g=R.value,{dashed:D,type:B,ghost:U,text:w,color:i,round:L,circle:X,textColor:S,secondary:ye,tertiary:te,quaternary:me,strong:ge}=e,we={"--n-font-weight":ge?Q:I};let s={"--n-color":"initial","--n-color-hover":"initial","--n-color-pressed":"initial","--n-color-focus":"initial","--n-color-disabled":"initial","--n-ripple-color":"initial","--n-text-color":"initial","--n-text-color-hover":"initial","--n-text-color-pressed":"initial","--n-text-color-focus":"initial","--n-text-color-disabled":"initial"};const K=B==="tertiary",ne=B==="default",a=K?"default":B;if(w){const l=S||i;s={"--n-color":"#0000","--n-color-hover":"#0000","--n-color-pressed":"#0000","--n-color-focus":"#0000","--n-color-disabled":"#0000","--n-ripple-color":"#0000","--n-text-color":l||o[t("textColorText",a)],"--n-text-color-hover":l?k(l):o[t("textColorTextHover",a)],"--n-text-color-pressed":l?N(l):o[t("textColorTextPressed",a)],"--n-text-color-focus":l?k(l):o[t("textColorTextHover",a)],"--n-text-color-disabled":l||o[t("textColorTextDisabled",a)]}}else if(U||D){const l=S||i;s={"--n-color":"#0000","--n-color-hover":"#0000","--n-color-pressed":"#0000","--n-color-focus":"#0000","--n-color-disabled":"#0000","--n-ripple-color":i||o[t("rippleColor",a)],"--n-text-color":l||o[t("textColorGhost",a)],"--n-text-color-hover":l?k(l):o[t("textColorGhostHover",a)],"--n-text-color-pressed":l?N(l):o[t("textColorGhostPressed",a)],"--n-text-color-focus":l?k(l):o[t("textColorGhostHover",a)],"--n-text-color-disabled":l||o[t("textColorGhostDisabled",a)]}}else if(ye){const l=ne?o.textColor:K?o.textColorTertiary:o[t("color",a)],u=i||l,A=B!=="default"&&B!=="tertiary";s={"--n-color":A?G(u,{alpha:Number(o.colorOpacitySecondary)}):o.colorSecondary,"--n-color-hover":A?G(u,{alpha:Number(o.colorOpacitySecondaryHover)}):o.colorSecondaryHover,"--n-color-pressed":A?G(u,{alpha:Number(o.colorOpacitySecondaryPressed)}):o.colorSecondaryPressed,"--n-color-focus":A?G(u,{alpha:Number(o.colorOpacitySecondaryHover)}):o.colorSecondaryHover,"--n-color-disabled":o.colorSecondary,"--n-ripple-color":"#0000","--n-text-color":u,"--n-text-color-hover":u,"--n-text-color-pressed":u,"--n-text-color-focus":u,"--n-text-color-disabled":u}}else if(te||me){const l=ne?o.textColor:K?o.textColorTertiary:o[t("color",a)],u=i||l;te?(s["--n-color"]=o.colorTertiary,s["--n-color-hover"]=o.colorTertiaryHover,s["--n-color-pressed"]=o.colorTertiaryPressed,s["--n-color-focus"]=o.colorSecondaryHover,s["--n-color-disabled"]=o.colorTertiary):(s["--n-color"]=o.colorQuaternary,s["--n-color-hover"]=o.colorQuaternaryHover,s["--n-color-pressed"]=o.colorQuaternaryPressed,s["--n-color-focus"]=o.colorQuaternaryHover,s["--n-color-disabled"]=o.colorQuaternary),s["--n-ripple-color"]="#0000",s["--n-text-color"]=u,s["--n-text-color-hover"]=u,s["--n-text-color-pressed"]=u,s["--n-text-color-focus"]=u,s["--n-text-color-disabled"]=u}else s={"--n-color":i||o[t("color",a)],"--n-color-hover":i?k(i):o[t("colorHover",a)],"--n-color-pressed":i?N(i):o[t("colorPressed",a)],"--n-color-focus":i?k(i):o[t("colorFocus",a)],"--n-color-disabled":i||o[t("colorDisabled",a)],"--n-ripple-color":i||o[t("rippleColor",a)],"--n-text-color":S||(i?o.textColorPrimary:K?o.textColorTertiary:o[t("textColor",a)]),"--n-text-color-hover":S||(i?o.textColorHoverPrimary:o[t("textColorHover",a)]),"--n-text-color-pressed":S||(i?o.textColorPressedPrimary:o[t("textColorPressed",a)]),"--n-text-color-focus":S||(i?o.textColorFocusPrimary:o[t("textColorFocus",a)]),"--n-text-color-disabled":S||(i?o.textColorDisabledPrimary:o[t("textColorDisabled",a)])};let Y={"--n-border":"initial","--n-border-hover":"initial","--n-border-pressed":"initial","--n-border-focus":"initial","--n-border-disabled":"initial"};w?Y={"--n-border":"none","--n-border-hover":"none","--n-border-pressed":"none","--n-border-focus":"none","--n-border-disabled":"none"}:Y={"--n-border":o[t("border",a)],"--n-border-hover":o[t("borderHover",a)],"--n-border-pressed":o[t("borderPressed",a)],"--n-border-focus":o[t("borderFocus",a)],"--n-border-disabled":o[t("borderDisabled",a)]};const{[t("height",g)]:J,[t("fontSize",g)]:Ce,[t("padding",g)]:ze,[t("paddingRound",g)]:$e,[t("iconSize",g)]:Be,[t("borderRadius",g)]:Se,[t("iconMargin",g)]:ke,waveOpacity:Pe}=o;return{"--n-bezier":n,"--n-bezier-ease-out":v,"--n-ripple-duration":T,"--n-opacity-disabled":H,"--n-wave-opacity":Pe,...we,...s,...Y,...{"--n-width":X&&!w?J:"initial","--n-height":w?"initial":J,"--n-font-size":Ce,"--n-padding":X||w?"initial":L?$e:ze,"--n-icon-size":Be,"--n-icon-margin":ke,"--n-border-radius":w?"initial":X||L?J:Se}}}),re=_?Qe("button",O(()=>{let n="";const{dashed:v,type:o,ghost:T,text:H,color:I,round:Q,circle:g,textColor:D,secondary:B,tertiary:U,quaternary:w,strong:i}=e;v&&(n+="a"),T&&(n+="b"),H&&(n+="c"),Q&&(n+="d"),g&&(n+="e"),B&&(n+="f"),U&&(n+="g"),w&&(n+="h"),i&&(n+="i"),I&&(n+=`j${de(I)}`),D&&(n+=`k${de(D)}`);const{value:L}=R;return n+=`l${L[0]}`,n+=`m${o[0]}`,n}),oe,e):void 0;return{selfElRef:h,waveElRef:p,mergedClsPrefix:r,mergedFocusable:E,mergedSize:R,showBorder:m,enterPressed:c,rtlEnabled:xe,handleMousedown:F,handleKeydown:be,handleBlur:ve,handleKeyup:he,handleClick:fe,customColorCssVars:O(()=>{const{color:n}=e;if(!n)return null;const v=k(n);return{"--n-border-color":n,"--n-border-color-hover":v,"--n-border-color-pressed":N(n),"--n-border-color-focus":v,"--n-border-color-disabled":n}}),cssVars:_?void 0:oe,themeClass:re?.themeClass,onRender:re?.onRender}},render(){const{mergedClsPrefix:e,tag:h,onRender:p}=this;p?.();const c=le(this.$slots.default,m=>m&&(x(),$("span",{class:P(`${e}-button__content`)},[C(()=>m)],2)));return x(),Z(h,{ref:"selfElRef",class:P([this.themeClass,`${e}-button`,`${e}-button--${this.type}-type`,`${e}-button--${this.mergedSize}-type`,this.rtlEnabled&&`${e}-button--rtl`,this.disabled&&`${e}-button--disabled`,this.block&&`${e}-button--block`,this.enterPressed&&`${e}-button--pressed`,!this.text&&this.dashed&&`${e}-button--dashed`,this.color&&`${e}-button--color`,this.secondary&&`${e}-button--secondary`,this.loading&&`${e}-button--loading`,this.ghost&&`${e}-button--ghost`]),tabindex:this.mergedFocusable?0:-1,type:this.attrType,style:M(this.cssVars),disabled:this.disabled,onClick:this.handleClick,onBlur:this.handleBlur,onMousedown:this.handleMousedown,onKeyup:this.handleKeyup,onKeydown:this.handleKeydown},{default:De(()=>[C(()=>this.iconPlacement==="right"&&c),ie(eo,{width:!0},{default:()=>le(this.$slots.icon,m=>(this.loading||this.renderIcon||m)&&(x(),$("span",{class:P(`${e}-button__icon`),style:M({margin:Ye(this.$slots.default)?"0":""})},[ie(Je,null,{default:()=>this.loading?(x(),Z(Ze,Ae({clsPrefix:e,key:"loading",class:`${e}-icon-slot`,strokeWidth:20},this.spinProps),null,16,["clsPrefix","class"])):(x(),$("div",{key:"icon",class:P(`${e}-icon-slot`),role:"none"},[this.renderIcon?(x(),$(se,{key:0},[C(()=>this.renderIcon())],64)):(x(),$(se,{key:1},[C(()=>m)],64))],2))},1024)],6)))},1024),C(()=>this.iconPlacement==="left"&&c),this.text?C(()=>null):(x(),Z(to,{key:0,ref:"waveElRef",clsPrefix:e},null,8,["clsPrefix"])),this.showBorder?(x(),$("div",{key:2,"aria-hidden":!0,class:P(`${e}-button__border`),style:M(this.customColorCssVars)},null,6)):C(()=>null),this.showBorder?(x(),$("div",{key:4,"aria-hidden":!0,class:P(`${e}-button__state-border`),style:M(this.customColorCssVars)},null,6)):C(()=>null)]),_:2},1032,["class","tabindex","type","style","disabled","onClick","onBlur","onMousedown","onKeyup","onKeydown"])}}),fo=so;export{so as B,fo as X,de as c};
