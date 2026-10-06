const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const path = require('path');
const html = fs.readFileSync(path.join(__dirname,'../docs/q123.html'),'utf8');
const code = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]).join('\n').replace('  loadMarketData();','  // Controlled by the test harness.');
function context(){
  const els = new Map();
  const get = id => {
    if(!els.has(id)) els.set(id,{value:{curAsset:'TQQQ',navInput:'100000000',fxInput:'1300',bpsInput:'0'}[id] || '',
      dataset:{},style:{},classList:{add(){},remove(){},toggle(){}},addEventListener(){},textContent:'',innerHTML:''});
    return els.get(id);
  };
  const c=vm.createContext({console:{warn(){}},Date,Number,Math,parseFloat,parseInt,
    document:{getElementById:get,querySelector:()=>null,querySelectorAll:()=>[],addEventListener(){}},window:{scrollTo(){}},setInterval(){},
    fetch:async()=>({ok:false})});
  vm.runInContext(code,c);
  return {c,get};
}
const state = {version:'Q123_CANONICAL_V2',as_of:'2026-10-05',mode:'BOOST',asset:'TQQQ',
  target_mode:'BOOST',target_asset:'TQQQ',cooldown_remaining:0,boost_days:20,boost_peak:800,boost_drawdown:-.055,above_days:125,
  next_session:'2099-01-05',next_open_utc:'2099-01-05T14:30:00Z',
  indicators:{close:756.2,sma50:718,sma200:669,ret63:.067,ret126:.28}};
function market(s){return {q123:s,nasdaq:{peak_price:756.2,drawdown_pct:0},etf_prices:{QLD:99.84},updated_utc:'2026-10-06T03:00:00Z'};}
async function load(s){const r=context();r.c.payload=market(s);vm.runInContext('fetch = async()=>({ok:true,json:async()=>payload})',r.c);await vm.runInContext('loadMarketData()',r.c);return r;}
(async()=>{
  // BOOST persists when entry momentum no longer holds; no stateless SELL.
  let {c,get}=await load(state);vm.runInContext('generateOrder()',c);assert.match(get('orderOutput').innerHTML,/HOLD/);
  // Next-open pending target is distinct from today's executed holding.
  ({c,get}=await load({...state,target_mode:'NORMAL',target_asset:'QLD'}));
  vm.runInContext('generateOrder()',c);assert.match(get('orderOutput').innerHTML,/TQQQ 전량 매도 → QLD 매수/);
  assert.match(get('orderOutput').innerHTML,/₩0/); // 0bps is honored
  // A completed recovery/cooldown state must not be overridden by high momentum.
  ({c,get}=await load({...state,mode:'BEAR',asset:'QQQ',target_mode:'BEAR',target_asset:'QQQ',indicators:{...state.indicators,ret63:.2}}));
  get('curAsset').value='QQQ';vm.runInContext('generateOrder()',c);assert.match(get('orderOutput').innerHTML,/HOLD/);
  // Expired next-open instructions are not offered as current executable orders.
  ({c,get}=await load({...state,next_open_utc:'2000-01-01T14:30:00Z'}));
  vm.runInContext('generateOrder()',c);assert.match(get('orderOutput').innerHTML,/이미 지났습니다/);
  // Missing or incompatible state stops order generation.
  ({c,get}=await load({...state,version:'OLD'}));
  vm.runInContext('generateOrder()',c);assert.match(get('orderOutput').innerHTML,/로딩되지 않았습니다/);
  console.log('Q123 UI: 5 regression scenarios passed');
})().catch(e=>{console.error(e);process.exitCode=1});
