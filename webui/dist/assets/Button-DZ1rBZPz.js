import{c as ie}from"./color-to-class-B0iQgAn2.js";import{d as ee,a8 as Te,a9 as He,aa as Re,V as f,ab as Ee,W as N,k as m,c as z,x as We,P as Fe,p as W,a0 as Ie,ac as ce,ad as _e,a2 as x,a1 as d,a3 as ae,X as ue,f as Z,w as De,b as se,a4 as O,Y as Le,i as j,ae as Ke,a5 as Me,F as le,af as Oe,a7 as t,ag as A}from"./index-Ci1All7C.js";import{n as k,j as je,k as Ae,i as Ge,r as de,e as C,u as Ne,a as Ve,b as qe,h as Qe,d as Xe,l as Ye,g as Ue,I as Je,L as Ze}from"./browser-CDhfpCLB.js";var eo=ee({name:"FadeInExpandTransition",props:{appear:Boolean,group:Boolean,mode:String,onLeave:Function,onAfterLeave:Function,onAfterEnter:Function,width:Boolean,reverse:Boolean},setup(e,{slots:h}){function p(r){e.width?r.style.maxWidth=`${r.offsetWidth}px`:r.style.maxHeight=`${r.offsetHeight}px`,r.offsetWidth}function c(r){e.width?r.style.maxWidth="0":r.style.maxHeight="0",r.offsetWidth;const{onLeave:b}=e;b&&b()}function y(r){e.width?r.style.maxWidth="":r.style.maxHeight="";const{onAfterLeave:b}=e;b&&b()}function V(r){if(r.style.transition="none",e.width){const b=r.offsetWidth;r.style.maxWidth="0",r.offsetWidth,r.style.transition="",r.style.maxWidth=`${b}px`}else if(e.reverse)r.style.maxHeight=`${r.offsetHeight}px`,r.offsetHeight,r.style.transition="",r.style.maxHeight="0";else{const b=r.offsetHeight;r.style.maxHeight="0",r.offsetWidth,r.style.transition="",r.style.maxHeight=`${b}px`}r.offsetWidth}function F(r){e.width?r.style.maxWidth="":e.reverse||(r.style.maxHeight=""),e.onAfterEnter?.()}return()=>{const{group:r,width:b,appear:q,mode:R}=e,E=r?He:Re,I={name:b?"fade-in-width-expand-transition":"fade-in-height-expand-transition",appear:q,onEnter:V,onAfterEnter:F,onBeforeLeave:p,onLeave:c,onAfterLeave:y};return r||(I.mode=R),Te(E,I,h)}}});const{cubicBezierEaseInOut:$}=Ee;function oo({duration:e=".2s",delay:h=".1s"}={}){return[f("&.fade-in-width-expand-transition-leave-from, &.fade-in-width-expand-transition-enter-to",{opacity:1}),f("&.fade-in-width-expand-transition-leave-to, &.fade-in-width-expand-transition-enter-from",`
 opacity: 0!important;
 margin-left: 0!important;
 margin-right: 0!important;
 `),f("&.fade-in-width-expand-transition-leave-active",`
 overflow: hidden;
 transition:
 opacity ${e} ${$},
 max-width ${e} ${$} ${h},
 margin-left ${e} ${$} ${h},
 margin-right ${e} ${$} ${h};
 `),f("&.fade-in-width-expand-transition-enter-active",`
 overflow: hidden;
 transition:
 opacity ${e} ${$} ${h},
 max-width ${e} ${$},
 margin-left ${e} ${$},
 margin-right ${e} ${$};
 `)]}var ro=N("base-wave",`
 position: absolute;
 left: 0;
 right: 0;
 top: 0;
 bottom: 0;
 border-radius: inherit;
`),to=ee({name:"BaseWave",props:{clsPrefix:{type:String,required:!0}},setup(e){je("-base-wave",ro,Ie(e,"clsPrefix"));const h=W(null),p=W(!1);let c=null;return We(()=>{c!==null&&window.clearTimeout(c)}),{active:p,selfRef:h,play(){c!==null&&(window.clearTimeout(c),p.value=!1,c=null),Fe(()=>{h.value?.offsetHeight,p.value=!0,c=window.setTimeout(()=>{p.value=!1,c=null},1e3)})}}},render(){const{clsPrefix:e}=this;return m(),z("div",{ref:"selfRef","aria-hidden":!0,class:k([`${e}-base-wave`,this.active&&`${e}-base-wave--active`])},null,2)}});function P(e){return ce(e,[255,255,255,.16])}function G(e){return ce(e,[0,0,0,.12])}const no=_e("n-button-group");var io=f([N("button",`
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
 `,[x("color",[d("border",{borderColor:"var(--n-border-color)"}),x("disabled",[d("border",{borderColor:"var(--n-border-color-disabled)"})]),ae("disabled",[f("&:focus",[d("state-border",{borderColor:"var(--n-border-color-focus)"})]),f("&:hover",[d("state-border",{borderColor:"var(--n-border-color-hover)"})]),f("&:active",[d("state-border",{borderColor:"var(--n-border-color-pressed)"})]),x("pressed",[d("state-border",{borderColor:"var(--n-border-color-pressed)"})])])]),x("disabled",{backgroundColor:"var(--n-color-disabled)",color:"var(--n-text-color-disabled)"},[d("border",{border:"var(--n-border-disabled)"})]),ae("disabled",[f("&:focus",{backgroundColor:"var(--n-color-focus)",color:"var(--n-text-color-focus)"},[d("state-border",{border:"var(--n-border-focus)"})]),f("&:hover",{backgroundColor:"var(--n-color-hover)",color:"var(--n-text-color-hover)"},[d("state-border",{border:"var(--n-border-hover)"})]),f("&:active",{backgroundColor:"var(--n-color-pressed)",color:"var(--n-text-color-pressed)"},[d("state-border",{border:"var(--n-border-pressed)"})]),x("pressed",{backgroundColor:"var(--n-color-pressed)",color:"var(--n-text-color-pressed)"},[d("state-border",{border:"var(--n-border-pressed)"})])]),x("loading","cursor: wait;"),N("base-wave",`
 pointer-events: none;
 top: 0;
 right: 0;
 bottom: 0;
 left: 0;
 animation-iteration-count: 1;
 animation-duration: var(--n-ripple-duration);
 animation-timing-function: var(--n-bezier-ease-out), var(--n-bezier-ease-out);
 `,[x("active",{zIndex:1,animationName:"button-wave-spread, button-wave-opacity"})]),Ae&&"MozBoxSizing"in document.createElement("div").style?f("&::moz-focus-inner",{border:0}):null,d("border, state-border",`
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
 `,[f("~",[d("icon",{margin:"var(--n-icon-margin)",marginRight:0})])]),x("block",`
 display: flex;
 width: 100%;
 `),x("dashed",[d("border, state-border",{borderStyle:"dashed !important"})]),x("disabled",{cursor:"not-allowed",opacity:"var(--n-opacity-disabled)"})]),f("@keyframes button-wave-spread",{from:{boxShadow:"0 0 0.5px 0 var(--n-ripple-color)"},to:{boxShadow:"0 0 0.5px 4.5px var(--n-ripple-color)"}}),f("@keyframes button-wave-opacity",{from:{opacity:"var(--n-wave-opacity)"},to:{opacity:0}})]);const ao={...ue.props,color:String,textColor:String,text:Boolean,block:Boolean,loading:Boolean,disabled:Boolean,circle:Boolean,size:String,ghost:Boolean,round:Boolean,secondary:Boolean,tertiary:Boolean,quaternary:Boolean,strong:Boolean,focusable:{type:Boolean,default:!0},keyboard:{type:Boolean,default:!0},tag:{type:String,default:"button"},type:{type:String,default:"default"},dashed:Boolean,renderIcon:Function,iconPlacement:{type:String,default:"left"},attrType:{type:String,default:"button"},bordered:{type:Boolean,default:!0},onClick:[Function,Array],nativeFocusBehavior:{type:Boolean,default:!Ye},spinProps:Object},so=ee({name:"Button",props:ao,slots:Object,setup(e){const h=W(null),p=W(null),c=W(!1),y=Le(()=>!e.quaternary&&!e.tertiary&&!e.secondary&&!e.text&&(!e.color||e.ghost||e.dashed)&&e.bordered),V=Ke(no,{}),{inlineThemeDisabled:F,mergedClsPrefixRef:r,mergedRtlRef:b,mergedComponentPropsRef:q}=Ne(e),{mergedSizeRef:R}=Ve({},{defaultSize:"medium",mergedSize:n=>{const{size:v}=e;if(v)return v;const{size:o}=V;if(o)return o;const{mergedSize:T}=n||{};if(T)return T.value;const H=q?.value?.Button?.size;return H||"medium"}}),E=j(()=>e.focusable&&!e.disabled),I=n=>{E.value||n.preventDefault(),!e.nativeFocusBehavior&&(n.preventDefault(),!e.disabled&&E.value&&h.value?.focus({preventScroll:!0}))},fe=n=>{if(!e.disabled&&!e.loading){const{onClick:v}=e;v&&Xe(v,n),e.text||p.value?.play()}},he=n=>{switch(n.key){case"Enter":if(!e.keyboard)return;c.value=!1}},be=n=>{switch(n.key){case"Enter":if(!e.keyboard||e.loading){n.preventDefault();return}c.value=!0}},ve=()=>{c.value=!1},pe=ue("Button","-button",io,Oe,e,r),me=qe("Button",b,r),oe=j(()=>{const{common:{cubicBezierEaseInOut:n,cubicBezierEaseOut:v},self:o}=pe.value,{rippleDuration:T,opacityDisabled:H,fontWeight:_,fontWeightStrong:Q}=o,g=R.value,{dashed:D,type:B,ghost:X,text:w,color:a,round:L,circle:Y,textColor:S,secondary:xe,tertiary:te,quaternary:ye,strong:ge}=e,we={"--n-font-weight":ge?Q:_};let s={"--n-color":"initial","--n-color-hover":"initial","--n-color-pressed":"initial","--n-color-focus":"initial","--n-color-disabled":"initial","--n-ripple-color":"initial","--n-text-color":"initial","--n-text-color-hover":"initial","--n-text-color-pressed":"initial","--n-text-color-focus":"initial","--n-text-color-disabled":"initial"};const K=B==="tertiary",ne=B==="default",i=K?"default":B;if(w){const l=S||a;s={"--n-color":"#0000","--n-color-hover":"#0000","--n-color-pressed":"#0000","--n-color-focus":"#0000","--n-color-disabled":"#0000","--n-ripple-color":"#0000","--n-text-color":l||o[t("textColorText",i)],"--n-text-color-hover":l?P(l):o[t("textColorTextHover",i)],"--n-text-color-pressed":l?G(l):o[t("textColorTextPressed",i)],"--n-text-color-focus":l?P(l):o[t("textColorTextHover",i)],"--n-text-color-disabled":l||o[t("textColorTextDisabled",i)]}}else if(X||D){const l=S||a;s={"--n-color":"#0000","--n-color-hover":"#0000","--n-color-pressed":"#0000","--n-color-focus":"#0000","--n-color-disabled":"#0000","--n-ripple-color":a||o[t("rippleColor",i)],"--n-text-color":l||o[t("textColorGhost",i)],"--n-text-color-hover":l?P(l):o[t("textColorGhostHover",i)],"--n-text-color-pressed":l?G(l):o[t("textColorGhostPressed",i)],"--n-text-color-focus":l?P(l):o[t("textColorGhostHover",i)],"--n-text-color-disabled":l||o[t("textColorGhostDisabled",i)]}}else if(xe){const l=ne?o.textColor:K?o.textColorTertiary:o[t("color",i)],u=a||l,M=B!=="default"&&B!=="tertiary";s={"--n-color":M?A(u,{alpha:Number(o.colorOpacitySecondary)}):o.colorSecondary,"--n-color-hover":M?A(u,{alpha:Number(o.colorOpacitySecondaryHover)}):o.colorSecondaryHover,"--n-color-pressed":M?A(u,{alpha:Number(o.colorOpacitySecondaryPressed)}):o.colorSecondaryPressed,"--n-color-focus":M?A(u,{alpha:Number(o.colorOpacitySecondaryHover)}):o.colorSecondaryHover,"--n-color-disabled":o.colorSecondary,"--n-ripple-color":"#0000","--n-text-color":u,"--n-text-color-hover":u,"--n-text-color-pressed":u,"--n-text-color-focus":u,"--n-text-color-disabled":u}}else if(te||ye){const l=ne?o.textColor:K?o.textColorTertiary:o[t("color",i)],u=a||l;te?(s["--n-color"]=o.colorTertiary,s["--n-color-hover"]=o.colorTertiaryHover,s["--n-color-pressed"]=o.colorTertiaryPressed,s["--n-color-focus"]=o.colorSecondaryHover,s["--n-color-disabled"]=o.colorTertiary):(s["--n-color"]=o.colorQuaternary,s["--n-color-hover"]=o.colorQuaternaryHover,s["--n-color-pressed"]=o.colorQuaternaryPressed,s["--n-color-focus"]=o.colorQuaternaryHover,s["--n-color-disabled"]=o.colorQuaternary),s["--n-ripple-color"]="#0000",s["--n-text-color"]=u,s["--n-text-color-hover"]=u,s["--n-text-color-pressed"]=u,s["--n-text-color-focus"]=u,s["--n-text-color-disabled"]=u}else s={"--n-color":a||o[t("color",i)],"--n-color-hover":a?P(a):o[t("colorHover",i)],"--n-color-pressed":a?G(a):o[t("colorPressed",i)],"--n-color-focus":a?P(a):o[t("colorFocus",i)],"--n-color-disabled":a||o[t("colorDisabled",i)],"--n-ripple-color":a||o[t("rippleColor",i)],"--n-text-color":S||(a?o.textColorPrimary:K?o.textColorTertiary:o[t("textColor",i)]),"--n-text-color-hover":S||(a?o.textColorHoverPrimary:o[t("textColorHover",i)]),"--n-text-color-pressed":S||(a?o.textColorPressedPrimary:o[t("textColorPressed",i)]),"--n-text-color-focus":S||(a?o.textColorFocusPrimary:o[t("textColorFocus",i)]),"--n-text-color-disabled":S||(a?o.textColorDisabledPrimary:o[t("textColorDisabled",i)])};let U={"--n-border":"initial","--n-border-hover":"initial","--n-border-pressed":"initial","--n-border-focus":"initial","--n-border-disabled":"initial"};w?U={"--n-border":"none","--n-border-hover":"none","--n-border-pressed":"none","--n-border-focus":"none","--n-border-disabled":"none"}:U={"--n-border":o[t("border",i)],"--n-border-hover":o[t("borderHover",i)],"--n-border-pressed":o[t("borderPressed",i)],"--n-border-focus":o[t("borderFocus",i)],"--n-border-disabled":o[t("borderDisabled",i)]};const{[t("height",g)]:J,[t("fontSize",g)]:Ce,[t("padding",g)]:$e,[t("paddingRound",g)]:ze,[t("iconSize",g)]:Be,[t("borderRadius",g)]:Se,[t("iconMargin",g)]:Pe,waveOpacity:ke}=o;return{"--n-bezier":n,"--n-bezier-ease-out":v,"--n-ripple-duration":T,"--n-opacity-disabled":H,"--n-wave-opacity":ke,...we,...s,...U,...{"--n-width":Y&&!w?J:"initial","--n-height":w?"initial":J,"--n-font-size":Ce,"--n-padding":Y||w?"initial":L?ze:$e,"--n-icon-size":Be,"--n-icon-margin":Pe,"--n-border-radius":w?"initial":Y||L?J:Se}}}),re=F?Qe("button",j(()=>{let n="";const{dashed:v,type:o,ghost:T,text:H,color:_,round:Q,circle:g,textColor:D,secondary:B,tertiary:X,quaternary:w,strong:a}=e;v&&(n+="a"),T&&(n+="b"),H&&(n+="c"),Q&&(n+="d"),g&&(n+="e"),B&&(n+="f"),X&&(n+="g"),w&&(n+="h"),a&&(n+="i"),_&&(n+=`j${ie(_)}`),D&&(n+=`k${ie(D)}`);const{value:L}=R;return n+=`l${L[0]}`,n+=`m${o[0]}`,n}),oe,e):void 0;return{selfElRef:h,waveElRef:p,mergedClsPrefix:r,mergedFocusable:E,mergedSize:R,showBorder:y,enterPressed:c,rtlEnabled:me,handleMousedown:I,handleKeydown:be,handleBlur:ve,handleKeyup:he,handleClick:fe,customColorCssVars:j(()=>{const{color:n}=e;if(!n)return null;const v=P(n);return{"--n-border-color":n,"--n-border-color-hover":v,"--n-border-color-pressed":G(n),"--n-border-color-focus":v,"--n-border-color-disabled":n}}),cssVars:F?void 0:oe,themeClass:re?.themeClass,onRender:re?.onRender}},render(){const{mergedClsPrefix:e,tag:h,onRender:p}=this;p?.();const c=de(this.$slots.default,y=>y&&(m(),z("span",{class:k(`${e}-button__content`)},[C(()=>y)],2)));return m(),Z(h,{ref:"selfElRef",class:k([this.themeClass,`${e}-button`,`${e}-button--${this.type}-type`,`${e}-button--${this.mergedSize}-type`,this.rtlEnabled&&`${e}-button--rtl`,this.disabled&&`${e}-button--disabled`,this.block&&`${e}-button--block`,this.enterPressed&&`${e}-button--pressed`,!this.text&&this.dashed&&`${e}-button--dashed`,this.color&&`${e}-button--color`,this.secondary&&`${e}-button--secondary`,this.loading&&`${e}-button--loading`,this.ghost&&`${e}-button--ghost`]),tabindex:this.mergedFocusable?0:-1,type:this.attrType,style:O(this.cssVars),disabled:this.disabled,onClick:this.handleClick,onBlur:this.handleBlur,onMousedown:this.handleMousedown,onKeyup:this.handleKeyup,onKeydown:this.handleKeydown},{default:De(()=>[C(()=>this.iconPlacement==="right"&&c),se(eo,{width:!0},{default:()=>de(this.$slots.icon,y=>(this.loading||this.renderIcon||y)&&(m(),z("span",{class:k(`${e}-button__icon`),style:O({margin:Ue(this.$slots.default)?"0":""})},[se(Je,null,{default:()=>this.loading?(m(),Z(Ze,Me({clsPrefix:e,key:"loading",class:`${e}-icon-slot`,strokeWidth:20},this.spinProps),null,16,["clsPrefix","class"])):(m(),z("div",{key:"icon",class:k(`${e}-icon-slot`),role:"none"},[this.renderIcon?(m(),z(le,{key:0},[C(()=>this.renderIcon())],64)):(m(),z(le,{key:1},[C(()=>y)],64))],2))},1024)],6)))},1024),C(()=>this.iconPlacement==="left"&&c),this.text?C(()=>null):(m(),Z(to,{key:0,ref:"waveElRef",clsPrefix:e},null,8,["clsPrefix"])),this.showBorder?(m(),z("div",{key:2,"aria-hidden":!0,class:k(`${e}-button__border`),style:O(this.customColorCssVars)},null,6)):C(()=>null),this.showBorder?(m(),z("div",{key:4,"aria-hidden":!0,class:k(`${e}-button__state-border`),style:O(this.customColorCssVars)},null,6)):C(()=>null)]),_:2},1032,["class","tabindex","type","style","disabled","onClick","onBlur","onMousedown","onKeyup","onKeydown"])}}),ho=so;export{so as B,ho as X};
