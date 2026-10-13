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

class ScopedIdentityTests(unittest.TestCase):
    def test_combined_issuer_keeps_only_requested_entity(self):
        first = document().replace('name="dei:EntityCentralIndexKey"', 'contextref="c" name="dei:EntityCentralIndexKey"').replace('name="dei:DocumentPeriodEndDate"', 'contextref="c" name="dei:DocumentPeriodEndDate"')
        second = first.replace('70858', '19617').replace('id="c"', 'id="other"').replace('contextref="c"', 'contextref="other"').replace('1,250', '9,999')
        result = parse(first + second)
        self.assertEqual(result['us-gaap:DebtCurrent']['units']['USD'][0]['val'], 1250000000)
        with self.assertRaises(ValueError):
            parse(first.replace('contextref="c" name="dei:', 'contextref="other" name="dei:') + second)
        with self.assertRaises(ValueError):
            parse(first.replace('June 30, 2026', 'March 31, 2026') + second)


class IssuerPDFTests(unittest.TestCase):
    cover = '''FORM 10-Q
For the quarterly period ended Commission file
June 30, 2026 number 1-5805
JPMorgan Chase & Co .
(Exact name of registrant as specified in its charter)
Common stock JPM The New York Stock Exchange'''
    highlights = '''JPMorgan Chase & Co.
Consolidated financial highlights (unaudited)
As of or for the period ended, Six months ended June 30,
ratio, employee data 2Q26 1Q26 4Q25 3Q25 2Q25 2026 2025
Common equity Tier 1 (“CET1”) capital ratio – Standardized (d) 14.2 14.3 14.6 14.8 15.1 14.2 15.1
Tier 1 capital ratio – Standardized (d) 15.1 15.2 15.5 15.8 16.1 15.1 16.1'''

    def parse(self, cover=None, highlights=None):
        return f.jpm_pdf_capital([cover or self.cover, 'Contents', highlights or self.highlights], '2026-06-30', 'issuer.pdf', '2026-08-01')

    def test_actual_current_column_and_provenance(self):
        result = self.parse()
        self.assertEqual(result['val'], 14.2)
        self.assertEqual(result['page'], 3)
        self.assertEqual(result['column'], '2Q26')
        self.assertEqual(result['filed'], '2026-08-01')
        self.assertIn('14.2 14.3', result['tableRow'])

    def test_wrong_identity_date_columns_and_nonactual_ratios(self):
        for cover in (self.cover.replace('JPMorgan Chase & Co .', 'JPMorgan Chase Bank, N.A.'),
                      self.cover.replace('June 30', 'March 31'), self.cover.replace('10-Q', '8-K'),
                      self.cover.replace('Common stock JPM', 'Common stock OTHER')):
            with self.subTest(cover=cover), self.assertRaises(ValueError): self.parse(cover=cover)
        for text in (self.highlights.replace('2Q26 1Q26', '1Q26 2Q26'),
                     self.highlights.replace('Standardized (d)', 'Regulatory minimum (d)'),
                     self.highlights.replace('14.2 14.3', '14.2 estimated 14.3'),
                     self.highlights.replace('JPMorgan Chase & Co.', 'JPMorgan Chase Bank, N.A.'),
                     self.highlights.replace('Common equity Tier 1 (“CET1”)', 'Tier 1'),
                     self.highlights.replace('14.2 14.3', '— 14.3')):
            with self.subTest(text=text), self.assertRaises(ValueError): self.parse(highlights=text)

    def test_conflicting_parent_highlights_rejected(self):
        with self.assertRaises(ValueError):
            f.jpm_pdf_capital([self.cover, self.highlights, self.highlights.replace('14.2', '14.1')], '2026-06-30', 'issuer.pdf', '2026-08-01')

    def test_pdf_fallback_after_blocked_archive(self):
        issuer = dict(filings=dict(recent=dict(form=['10-Q'], filingDate=['2026-08-01'], accessionNumber=['0001628280-26-054343'], primaryDocument=['jpm-20260630.htm'], reportDate=['2026-06-30'])))
        pdf = dict(facts={}, cet1=self.parse(), source='issuer.pdf', attempts=[])
        with patch.object(f.v, 'fetch', side_effect=ValueError('blocked')), patch.object(f, 'issuer_pdf', return_value=pdf) as fetch:
            result = f.recover(dict(cik=19617), issuer)
        self.assertEqual(result['cet1']['val'], 14.2)
        fetch.assert_called_once_with(dict(cik=19617), '2026-06-30', '2026-08-01', '10-Q')
        self.assertTrue(result['attempts'])

if __name__=='__main__': unittest.main()
