"""Read-only option definitions and owned snapshots on the existing IB owner loop."""
import asyncio
import datetime as dt
import re
import time
from vmi_data import symbol


def contract(detail, under):
    c = detail.contract
    expiry = c.lastTradeDateOrContractMonth
    local = re.fullmatch(r'(.{6})(\d{6})C(\d{8})', c.localSymbol or '')
    if not (under.secType == 'STK' and under.currency == 'USD' and
            c.secType == 'OPT' and c.right == 'C' and c.currency == 'USD' and
            c.symbol == under.symbol and c.tradingClass == under.symbol and
            str(c.multiplier) in ('100', '100.0') and c.conId > 0 and
            detail.underConId == under.conId and detail.underSecType == 'STK' and
            re.fullmatch(r'\d{8}', expiry or '') and
            expiry >= dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d') and
            c.strike > 0 and local and local[1].strip() == under.symbol and
            local[2] == expiry[2:] and int(local[3]) == round(c.strike * 1000)):
        raise ValueError('Adjusted, expired or ambiguous call contract excluded')
    return dict(conid=c.conId, symbol=c.localSymbol, secType='OPT', right='C',
                root=under.symbol, underConid=under.conId, strike=c.strike, expiry=expiry,
                currency='USD', mult=100, exch='SMART', tradingClass=c.tradingClass,
                deliverableStatus='IB_STANDARD_CLASS_NOT_OCC_VERIFIED')


def unchanged(engine, ib, generation):
    if engine._ib is not ib or engine.info.get('generation', 0) != generation:
        raise ConnectionError('Gateway changed during option qualification')


async def chain(engine, ticker, expiry=None):
    from ib_async import Option
    ticker = symbol(ticker)
    ib, generation = engine._ib, engine.info.get('generation', 0)
    under = await engine._stock(ticker)
    params = await asyncio.wait_for(ib.reqSecDefOptParamsAsync(under.symbol, '', 'STK', under.conId), 20)
    params = [p for p in params if p.exchange == 'SMART' and p.tradingClass == under.symbol and str(p.multiplier) in ('100', '100.0')]
    expirations = sorted({e for p in params for e in p.expirations if re.fullmatch(r'\d{8}', e) and e >= dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d')})
    out = dict(symbol=ticker, underConid=under.conId, expirations=expirations, calls=[], excluded=0, served_by='tws')
    if expiry:
        if expiry not in expirations:
            raise ValueError('Expiry is not listed in the matching IB standard class')
        template = Option(under.symbol, expiry, 0, 'C', 'SMART', tradingClass=under.symbol, multiplier='100', currency='USD')
        # Never use the legacy disk chain; it loses underlying/multiplier metadata.
        details = await asyncio.wait_for(ib.reqContractDetailsAsync(template), 60)
        for detail in details:
            try:
                row = contract(detail, under)
                if row['expiry'] != expiry:
                    raise ValueError('Wrong expiry')
                out['calls'].append(row)
            except ValueError:
                out['excluded'] += 1
        out['calls'].sort(key=lambda c: c['strike'])
        out['expiry'] = expiry
    unchanged(engine, ib, generation)
    out['retrievedAt'] = int(time.time()*1000)
    return out


async def quote(engine, ticker, conid):
    from ib_async import Contract
    ticker = symbol(ticker)
    if isinstance(conid, bool) or not isinstance(conid, int) or not 0 < conid < 2**53:
        raise ValueError('Invalid option contract ID')
    if not hasattr(engine, '_vmi_option_lock'):
        engine._vmi_option_lock = asyncio.Lock()
    async with engine._vmi_option_lock:
        ib, generation = engine._ib, engine.info.get('generation', 0)
        under = await engine._stock(ticker)
        details = await asyncio.wait_for(ib.reqContractDetailsAsync(Contract(conId=conid, exchange='SMART')), 20)
        if len(details) != 1 or details[0].contract.conId != conid:
            raise ValueError('Ambiguous option identity')
        inst = contract(details[0], under)
        unchanged(engine, ib, generation)
        c = details[0].contract
        c.exchange = 'SMART'
        q = await engine._snapshot_quote(c)
        unchanged(engine, ib, generation)
        if q.get('brokerConid') != conid:
            raise ValueError('Option quote contract mismatch')
        return dict(inst=inst, quote=q, served_by='tws', retrievedAt=int(time.time()*1000))
