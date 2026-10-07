import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from market import MarketEngine
import vmi_options as v

class OptionTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self):
        from ib_async import Stock, Option, ContractDetails
        e=MarketEngine()
        u=Stock('META','SMART','USD',conId=123)
        c=Option('META','20990116',100,'C','SMART',multiplier='100',currency='USD',conId=456,tradingClass='META',localSymbol='META  990116C00100000')
        d=ContractDetails(contract=c,underConId=123,underSecType='STK')
        e._stock=AsyncMock(return_value=u)
        e._ib=SimpleNamespace(reqContractDetailsAsync=AsyncMock(return_value=[d]),reqSecDefOptParamsAsync=AsyncMock(return_value=[SimpleNamespace(exchange='SMART',tradingClass='META',multiplier='100',expirations={'20990116'})]))
        e._snapshot_quote=AsyncMock(return_value={'brokerConid':456,'bid':2,'ask':3,'status':'LIVE','at':123})
        return e,u,d

    async def test_verified_contract_and_owned_snapshot(self):
        e,u,d=self.fixture()
        out=await e._vmi_option('META',456)
        self.assertEqual(out['inst']['underConid'],123)
        self.assertEqual(out['inst']['deliverableStatus'],'IB_STANDARD_CLASS_NOT_OCC_VERIFIED')
        self.assertEqual(out['quote']['bid'],2)
        e._snapshot_quote.assert_awaited_once_with(d.contract)
        e._snapshot_quote.return_value['brokerConid']=789
        with self.assertRaises(ValueError): await e._vmi_option('META',456)

    async def test_adjusted_or_incomplete_details_never_quoted(self):
        for field,value in [('multiplier','10'),('right','P'),('currency','EUR'),('tradingClass','META1'),('localSymbol','META1 990116C00100000'),('lastTradeDateOrContractMonth','20200101')]:
            e,u,d=self.fixture();setattr(d.contract,field,value)
            with self.assertRaises(ValueError): await e._vmi_option('META',456)
            e._snapshot_quote.assert_not_awaited()
        e,u,d=self.fixture();d.underConId=999
        with self.assertRaises(ValueError): await e._vmi_option('META',456)
        for cid in [True,-1,0,'456']:
            with self.assertRaises(ValueError): await e._vmi_option('META',cid)

    async def test_full_chain_details_not_lossy_legacy_disk(self):
        e,u,d=self.fixture()
        with patch('tws._disk',side_effect=AssertionError('Must not use legacy disk')):
            out=await e._vmi_chain('META','20990116')
        self.assertEqual(out['calls'][0]['conid'],456)
        with self.assertRaises(ValueError): await e._vmi_chain('META','20990117')
        d.contract.multiplier='10'
        out=await e._vmi_chain('META','20990116')
        self.assertEqual(out['calls'],[])
        self.assertEqual(out['excluded'],1)

    async def test_gateway_change_rejected(self):
        e,u,d=self.fixture()
        async def changing(*args):
            e.info['generation']=e.info.get('generation',0)+1
            return [d]
        e._ib.reqContractDetailsAsync=changing
        with self.assertRaises(ConnectionError): await e._vmi_option('META',456)
        e._snapshot_quote.assert_not_awaited()

if __name__=='__main__': unittest.main()
