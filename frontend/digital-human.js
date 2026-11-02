(()=>{'use strict';
class BankAvatar extends HTMLElement{
 connectedCallback(){if(this.dataset.loaded)return;this.dataset.loaded='1';this.classList.add('avatar','is-idle');this.setAttribute('role','img');this.setAttribute('aria-label','知小行，知行银行数字服务伙伴');fetch('/static/digital-human.svg').then(r=>{if(!r.ok)throw Error();return r.text();}).then(svg=>{this.innerHTML=svg;}).catch(()=>{this.textContent='知小行';});}
}
customElements.define('bank-avatar',BankAvatar);
let state='idle',speechEnabled=false,utterance=null;
const labels={idle:'随时为你服务',thinking:'正在理解你的需求',guiding:'为你解答',awaiting:'等待你的授权',speaking:'正在为你讲解',attention:'请查看安全提示',paused:'任务已暂停'};
function setState(next,label){state=next in labels?next:'idle';document.querySelectorAll('bank-avatar').forEach(el=>{el.className='avatar is-'+state;});document.querySelectorAll('[data-human-status]').forEach(el=>{el.textContent=label||labels[state];});const stage=document.querySelector('.human-stage');if(stage)stage.dataset.state=state;}
function localVoice(){return window.speechSynthesis?.getVoices().find(v=>v.localService&&/^zh/i.test(v.lang));}
function stop(){if(window.speechSynthesis)window.speechSynthesis.cancel();utterance=null;if(state==='speaking')setState('idle');}
function speak(text){if(!speechEnabled||!localVoice()||!window.SpeechSynthesisUtterance)return;stop();utterance=new SpeechSynthesisUtterance(text);utterance.voice=localVoice();utterance.lang='zh-CN';utterance.rate=1;utterance.onstart=()=>setState('speaking');utterance.onend=()=>setState('guiding');utterance.onerror=()=>setState('guiding');window.speechSynthesis.speak(utterance);}
function answer(out){const d=out.digital_human;if(d){setState(d.state,d.label);if(d.state!=='awaiting')speak(d.speech_text);}else setState('idle');}
function init(){const toggle=document.getElementById('voiceToggle'),hint=document.getElementById('voiceHint');if(!toggle)return;function availability(){const available=Boolean(localVoice());toggle.disabled=!available;if(hint)hint.textContent=available?'仅使用本机中文语音，默认关闭。':'本机未提供可用中文语音，文字服务正常。';}availability();if(window.speechSynthesis)window.speechSynthesis.addEventListener('voiceschanged',availability);toggle.addEventListener('change',()=>{speechEnabled=toggle.checked;if(!speechEnabled)stop();});document.getElementById('stopVoice')?.addEventListener('click',stop);}
addEventListener('pagehide',stop);document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});document.addEventListener('DOMContentLoaded',init);window.DigitalHuman={setState,answer,stop};
})();
