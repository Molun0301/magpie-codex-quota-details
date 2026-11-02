/* 验证混合档位、缓存与独立自定义价。 */
const {test}=require('node:test');
const assert=require('node:assert/strict');
require('../page/pricing.js');
const P=globalThis.quotaPricing;
const flat=(n)=>({input:n,output:n*5,cache_write:n*1.25,cache_read:n*.1});
const price=(n)=>({base:flat(n),tiers:[{above:272000,...flat(n*2)}]});
const usage={input:100000,output:10000,cached:200000,written:0,writtenHour:0,requests:1};
const tariff=(tier,n)=>({service:{tier,evidence:'served'},price:price(n),requests:1,tierUsage:{272000:usage}});
const standard=tariff('standard',2),fast=tariff('fast',4);
const model={name:'test',requests:2,tariffs:[standard,fast]};
const settings=P.normalize();
test('混合标准快速费用分别计算，缓存只计一次',()=>assert.equal(P.modelCost(model,settings),2.04));
test('标准自定义价不覆盖快速价',()=>{
  const own=P.normalize({schema:2,models:{test:{longContext:false,price:price(10)}}});
  assert.equal(own.schema,3);assert.deepEqual(own.models.test.price,price(10));
  assert.equal(P.modelCost(model,own),3.06);
});
test('只设置快速价格可回读，标准价不变',()=>{
  const own=P.normalize({schema:3,models:{test:{modes:{fast:{longContext:false,price:price(10)}}}}});
  assert.equal(P.modelCost(model,own),2.38);
  assert.deepEqual(P.normalize(JSON.parse(JSON.stringify(own))),own);
});
test('未知档位显式使用基准价，未知服务无价时不漏算',()=>{
  assert.equal(P.tariffCost({...standard,service:{tier:'unknown'}},null),.68);
  assert.equal(P.tariffCost({...fast,price:null},null),null);
});
test('快速长上下文缺档时显示未知',()=>{
  assert.equal(P.tariffCost({...fast,price:{base:flat(4),tiers:[]}},null),null);
});
test('词元分档不完整时拒绝部分总额',()=>{
  assert.equal(P.modelCost({...model,requests:3},settings),null);
});
test('缓存写入长保留单价覆盖且不重复计算',()=>{
  assert.equal(P.costAt({input:0,output:0,cached:0,written:1000000,writtenHour:400000},{...flat(2),cache_write_1h:5}),3.5);
});
test('自定义空价不转成免费',()=>{
  assert.throws(()=>P.normalizeModel({price:{base:{...flat(2),input:''},tiers:[]}}),/不能为空/);
});
