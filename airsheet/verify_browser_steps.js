const fs=require('fs');
const path=require('path');
const assert=require('assert/strict');
const source=fs.readFileSync(path.join(__dirname,'browser_steps.js'),'utf8');
const {startWpsDayRead}=new Function(source+'\nreturn {startWpsDayRead};')();
function mock(actual) {
  const calls={runs:0,sharedSelections:0,keys:[]};
  const title={count:async()=>1,waitFor:async()=>{},click:async()=>{calls.sharedSelections++;}};
  const shared={filter(){return this;},getByText(){return title;},locator(){throw Error('unexpected shared mutation');}};
  const header={filter(){return this;}};
  const frame={locator(selector){
    if(selector==='div.listItem')return shared;
    if(selector==='div.header-title')return header;
    if(selector==='button#run-script')return{click:async()=>{calls.runs++;}};
    throw Error('unexpected selector '+selector);
  },getByRole(role){
    if(role==='textbox')return{press:async key=>{assert(['ControlOrMeta+A','ControlOrMeta+C'].includes(key));calls.keys.push(key);}};
    throw Error('saving shared source is forbidden');
  }};
  const tab={playwright:{locator:()=>({count:async()=>1}),frameLocator:()=>frame},
    clipboard:{readText:async()=>actual,writeText:async()=>{throw Error('pasting shared source is forbidden');}},
    getAXState:async()=>{}};
  return{tab,calls};
}
(async()=>{
  const expected='const SCRIPT_VERSION = "1.1";\nreturn {};';
  let m=mock(expected+'\r\n');await startWpsDayRead(m.tab,expected);assert.equal(m.calls.runs,1);assert.equal(m.calls.sharedSelections,1);
  m=mock(expected+'\n// altered');await assert.rejects(startWpsDayRead(m.tab,expected),/shared_source_changed/);assert.equal(m.calls.runs,0);
  m=mock(expected);await assert.rejects(startWpsDayRead(m.tab,expected,'2026-09-21'),/historical_parameter_ui_not_configured/);assert.equal(m.calls.sharedSelections,0);
  m=mock(expected);await assert.rejects(startWpsDayRead(m.tab,'frozen personal source'),/wrong_shared_source/);assert.equal(m.calls.runs,0);
  console.log('4 shared-browser protection scenarios passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
