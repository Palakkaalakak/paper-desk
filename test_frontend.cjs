// Offline frontend adapter regressions: node --test test_frontend.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

const html = fs.readFileSync(__dirname + '/paper_local.html', 'utf8');
const source = html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];

test('entire inline application compiles', () => {
  assert.doesNotThrow(() => new vm.Script(source));
});

function portfolioFixture(saved) {
  const S = saved || {account:{name:'Original',type:'margin'},cash:99995,realized:-5,
    positions:[],orders:[],trades:[{realized:-5}],cashflows:[],equity:[],watchlist:[],log:[]};
  const ctx = vm.createContext({S, guns:null, trading:null, WHATIF:null, sel:null, CHAIN_SEQ:0,
    chainBusy:false, PARAMS:{}, CH:{}, OPTMETA:{}, money:String,
    save(){},render(){},setStatus(){},warn(){}});
  vm.runInContext(source.slice(source.indexOf('var BOOK_FIELDS'),source.indexOf('var renderPending')),ctx);
  ctx.bookInit();
  return ctx;
}
test('portfolio mode migration preserves existing cash, history and GUNS behavior', () => {
  const c=portfolioFixture();
  assert.equal(c.S.account.mode,'GUNS');
  assert.equal(c.S.cash,99995);
  assert.equal(c.S.trades[0].realized,-5);
  assert.equal(c.S.books[0].data.account.mode,'GUNS');
  c.bookInit();assert.equal(c.S.books.length,1);
});
test('Trading and Custom portfolio creation, switching and reload retain isolated modes and balances', () => {
  const c=portfolioFixture();
  const trading=c.bookCreate('ATR',100000,'Trading');
  assert.equal(c.S.account.mode,'Trading');c.S.cash=100123;
  const custom=c.bookCreate('Manual',20000,'Custom');
  assert.equal(c.S.account.mode,'Custom');assert.equal(c.S.cash,20000);
  c.bookSwitch(trading);assert.equal(c.S.cash,100123);assert.equal(c.S.account.mode,'Trading');
  c.bookSync();const restored=portfolioFixture(JSON.parse(JSON.stringify(c.S)));
  assert.equal(restored.S.account.mode,'Trading');restored.bookSwitch(custom);
  assert.equal(restored.S.cash,20000);assert.equal(restored.S.account.mode,'Custom');
  restored.bookSwitch('b1');assert.equal(restored.S.cash,99995);
  assert.equal(restored.S.trades[0].realized,-5);
});

test('option expirations contain each probed month only once', async () => {
  // Evaluate the real gateway adapter, not a copy of its implementation.
  const adapter = source.slice(source.indexOf('var GW_ACCOUNTS'), source.indexOf('var STREAM'));
  const ctx = vm.createContext({setTimeout});
  vm.runInContext(adapter, ctx);
  const months = ['JAN27', 'FEB27', 'MAR27', 'APR27', 'MAY27', 'JUN27',
                  'JUL27', 'AUG27', 'SEP27', 'OCT27'];
  ctx.gwFetch = async path => {
    if (path.startsWith('/trsrv/secdef')) return {secdef: [{ticker: 'AAPL'}]};
    return [];
  };
  ctx.gwSecdef = async () => [{conid: 123, sections: [
    {secType: 'OPT', months: months.join(';'), exchange: 'SMART'}
  ]}];
  const probed = [];
  ctx.gwMaturities = async (_, month) => {
    probed.push(month);
    return [ctx.gwThirdFriday(month)];
  };
  const result = await ctx.callGW('get_option_parameters', {underlying_contract_id: 123});
  const dates = Array.from(result.expirations, expiry => expiry.date);
  assert.equal(probed.length, 8);
  assert.equal(dates.length, months.length);
  assert.equal(new Set(dates).size, dates.length);
  assert.deepEqual(dates, [...dates].sort());
});

test('option expirations retain weekly dates in probed months', async () => {
  const adapter = source.slice(source.indexOf('var GW_ACCOUNTS'), source.indexOf('var STREAM'));
  const ctx = vm.createContext({setTimeout});
  vm.runInContext(adapter, ctx);
  ctx.gwFetch = async () => ({secdef: [{ticker: 'AAPL'}]});
  ctx.gwSecdef = async () => [{conid: 123, sections: [
    {secType: 'OPT', months: 'JAN27', exchange: 'SMART'}
  ]}];
  ctx.gwMaturities = async () => ['20270108', '20270115', '20270122'];
  const result = await ctx.callGW('get_option_parameters', {underlying_contract_id: 123});
  assert.deepEqual(Array.from(result.expirations, x => x.date),
                   ['20270108', '20270115', '20270122']);
  assert.equal(result.current_expiration, '123|JAN27|SMART|20270115');
});
