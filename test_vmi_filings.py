import unittest
from unittest.mock import patch
import vmi_filings as f


def document(value='1,250', dimension='', currency='USD', extra=''):
    return f'''<ix:nonnumeric name="dei:EntityCentralIndexKey">70858</ix:nonnumeric>
    <ix:nonnumeric name="dei:DocumentPeriodEndDate">June 30, 2026</ix:nonnumeric>
    <xbrli:context id="c"><xbrli:entity><xbrli:identifier>70858</xbrli:identifier>{dimension}</xbrli:entity>
    <xbrli:period><xbrli:instant>2026-06-30</xbrli:instant></xbrli:period></xbrli:context>
    <xbrli:unit id="u"><xbrli:measure>iso4217:{currency}</xbrli:measure></xbrli:unit>
    <ix:nonfraction name="us-gaap:DebtCurrent" contextref="c" unitref="u" scale="6" decimals="-6" format="ixt:num-dot-decimal">{value}</ix:nonfraction>{extra}'''


def parse(text):
    p=f.InlineFacts();p.feed(text)
    return p.validated(70858,'2026-06-30','issuer','2026-08-01','10-Q')


class FilingTests(unittest.TestCase):
    def test_scale_identity_date(self):
        row=parse(document())['us-gaap:DebtCurrent']['units']['USD'][0]
        self.assertEqual(row['val'],1250000000)
        self.assertEqual(row['source'],'issuer')
        with self.assertRaises(ValueError):parse(document().replace('>70858<','>19617<'))
        with self.assertRaises(ValueError):parse(document().replace('June 30, 2026','March 31, 2026'))

    def test_dimensions_currency_and_missing_values_rejected(self):
        self.assertFalse(parse(document(dimension='<xbrli:segment><xbrldi:explicitMember>Subsidiary</xbrldi:explicitMember></xbrli:segment>')))
        self.assertFalse(parse(document(currency='EUR')))
        self.assertFalse(parse(document(value='—')))
        self.assertFalse(parse(document().replace('num-dot-decimal','num-comma-decimal')))
        self.assertEqual(parse(document(value='—').replace('num-dot-decimal','zerodash'))['us-gaap:DebtCurrent']['units']['USD'][0]['val'],0)

    def test_conflicts_and_precision(self):
        duplicate='<ix:nonfraction name="us-gaap:DebtCurrent" contextref="c" unitref="u" scale="6" decimals="-6">1300</ix:nonfraction>'
        self.assertFalse(parse(document(extra=duplicate)))
        rounded=duplicate.replace('decimals="-6"','decimals="-8"')
        self.assertEqual(parse(document(extra=rounded))['us-gaap:DebtCurrent']['units']['USD'][0]['val'],1250000000)

    def test_namespace_preserved(self):
        self.assertNotIn('us-gaap:DebtCurrent',parse(document().replace('us-gaap:DebtCurrent','custom:DebtCurrent')))

    def test_capital_not_subsidiary_minimum_or_previous_period(self):
        def table(name,value):
            return f'''<table><tr><td>{name} Regulatory Capital</td></tr><tr><td>StandardizedApproach</td><td>AdvancedApproaches</td><td>RegulatoryMinimum</td></tr>
            <tr><td>June 30, 2026</td></tr><tr><td>Common equity tier 1 capital ratio</td><td>{value}</td><td>%</td><td>14</td><td>%</td><td>10</td><td>%</td></tr>
            <tr><td>December 31, 2025</td></tr><tr><td>Common equity tier 1 capital ratio</td><td>11.4</td><td>%</td></tr></table>'''
        text=table('Bank of America, N.A.',12.1)+table('Bank of America Corporation',11.2)
        self.assertEqual(f.capital_table(text,'Bank of America Corporation','2026-06-30','issuer')['val'],11.2)
        self.assertIsNone(f.capital_table(text,'Bank of America Corporation','2026-03-31','issuer'))
        self.assertIsNone(f.capital_table(text.replace('Common equity tier 1','Tier 1'),'Bank of America Corporation','2026-06-30','issuer'))

    def test_issuer_mirror_uses_sec_accession(self):
        issuer=dict(name='BANK OF AMERICA CORP /DE/',filings=dict(recent=dict(form=['10-Q'],filingDate=['2026-08-01'],accessionNumber=['0000070858-26-000394'],primaryDocument=['bac-20260630.htm'],reportDate=['2026-06-30'])))
        with patch.object(f.v,'fetch',return_value=document()) as fetch: result=f.recover(dict(cik=70858),issuer)
        self.assertIn('us-gaap:DebtCurrent',result['facts'])
        self.assertTrue(fetch.call_args.args[0].startswith(f.MIRRORS[70858]))

if __name__=='__main__': unittest.main()
