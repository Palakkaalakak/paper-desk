"""Read-only option definitions and owned snapshots on the existing IB owner loop."""
import asyncio
import datetime as dt
import re
import time
from vmi_data import symbol

DETAILS_TTL = 6 * 3600


def contract(detail, under, right='C'):
    c = detail.contract
    expiry = c.lastTradeDateOrContractMonth
    if right not in ('C', 'P'):
        raise ValueError('Invalid option right')
    local = re.fullmatch(r'(.{6})(\d{6})' + right + r'(\d{8})', c.localSymbol or '')
    if not (under.secType == 'STK' and under.currency == 'USD' and
            c.secType == 'OPT' and c.right == right and c.currency == 'USD' and
            c.symbol == under.symbol and c.tradingClass == under.symbol and
            str(c.multiplier) in ('100', '100.0') and c.conId > 0 and
            detail.underConId == under.conId and detail.underSecType == 'STK' and
            re.fullmatch(r'\d{8}', expiry or '') and
            expiry >= dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d') and
            c.strike > 0 and local and local[1].strip() == under.symbol and
            local[2] == expiry[2:] and int(local[3]) == round(c.strike * 1000)):
        raise ValueError('Adjusted, expired or ambiguous call contract excluded')
    return dict(conid=c.conId, symbol=c.localSymbol, secType='OPT', right=right,
                root=under.symbol, underConid=under.conId, strike=c.strike, expiry=expiry,
                currency='USD', mult=100, exch='SMART', tradingClass=c.tradingClass,
                deliverableStatus='IB_STANDARD_CLASS_NOT_OCC_VERIFIED')


def unchanged(engine, ib, generation):
    if engine._ib is not ib or engine.info.get('generation', 0) != generation:
        raise ConnectionError('Gateway changed during option qualification')


async def chain(engine, ticker, expiry=None, right='C'):
    from ib_async import Option
    ticker = symbol(ticker)
    ib, generation = engine._ib, engine.info.get('generation', 0)
    under = await engine._stock(ticker)
    if not hasattr(engine, '_vmi_option_params'):
        engine._vmi_option_params = {}
    key = (under.conId, generation)
    cached = engine._vmi_option_params.get(key)
    if cached and time.monotonic()-cached[0]<900:
        params = cached[1]
    else:
        params = await asyncio.wait_for(ib.reqSecDefOptParamsAsync(under.symbol, '', 'STK', under.conId), 20) or []
        unchanged(engine, ib, generation)
        if not params:
            # Usually a sec-def farm hiccup: never remember an empty chain.
            raise ValueError('IB returned no option parameters for %s; the security-definition farm may be '
                             'reconnecting. Try again in a minute.' % ticker)
        engine._vmi_option_params[key] = (time.monotonic(), params)
        if len(engine._vmi_option_params)>100:
            engine._vmi_option_params.pop(next(iter(engine._vmi_option_params)))
    params = [p for p in params if p.exchange == 'SMART' and p.tradingClass == under.symbol and str(p.multiplier) in ('100', '100.0')]
    expirations = sorted({e for p in params for e in p.expirations if re.fullmatch(r'\d{8}', e) and e >= dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d')})
    out = dict(symbol=ticker, underConid=under.conId, expirations=expirations, calls=[], excluded=0, served_by='tws')
    field = 'calls' if right == 'C' else 'puts'
    if right not in ('C', 'P'):
        raise ValueError('Invalid option right')
    out[field] = []
    if expiry:
        if expiry not in expirations:
            raise ValueError('Expiry is not listed in the matching IB standard class')
        # Never use the legacy disk chain; it loses underlying/multiplier metadata.
        # IB paces derivative contract-detail lookups and holds repeats, so keep the
        # verified details (conIds never change) across reconnects for a few hours.
        if not hasattr(engine, '_vmi_option_details'):
            engine._vmi_option_details = {}
        dkey = (under.conId, expiry, right)
        hit = engine._vmi_option_details.get(dkey)
        if hit and time.monotonic()-hit[0] < DETAILS_TTL:
            details = hit[1]
        else:
            template = Option(under.symbol, expiry, 0, right, 'SMART', tradingClass=under.symbol, multiplier='100', currency='USD')
            details = await asyncio.wait_for(ib.reqContractDetailsAsync(template), 60) or []
            unchanged(engine, ib, generation)
            if not details:
                raise ValueError('IB returned no %s %s contracts for %s; try again in a minute.'
                                 % (expiry, 'put' if right == 'P' else 'call', ticker))
            engine._vmi_option_details[dkey] = (time.monotonic(), details)
            if len(engine._vmi_option_details) > 200:
                engine._vmi_option_details.pop(next(iter(engine._vmi_option_details)))
        for detail in details:
            try:
                row = contract(detail, under, right)
                if row['expiry'] != expiry:
                    raise ValueError('Wrong expiry')
                out[field].append(row)
            except ValueError:
                out['excluded'] += 1
        out[field].sort(key=lambda c: c['strike'])
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
