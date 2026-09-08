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
