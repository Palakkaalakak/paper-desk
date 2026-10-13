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


class JPMCreditTests(unittest.TestCase):
    def fixture(self):
        def table(name,heading,rows):
            rows=[[name],['(in millions)',heading],['Jun 30,2026','Dec 31,2025','Jun 30,2026','Dec 31,2025']]+rows
            return '<table>'+''.join('<tr>'+''.join('<td>'+c+'</td>' for c in row)+'</tr>' for row in rows)+'</table>'
        return ('Nonperforming assets include nonaccrual loans, nonperforming derivatives and certain assets acquired in loan satisfactions'+
            table('Consumer credit portfolio','Credit exposure Nonaccrual loans(i)',[
                ['Total consumer, excluding credit card loans','391743','402258','4448','4673'],
                ['Total credit card loans','249876','247797','NA','NA'],
                ['Total consumer credit portfolio','1915166','1871408','4448','4673']])+
            table('Wholesale credit portfolio','Credit exposure Nonperforming',[
                ['Loans','900843','843374','4916','5184'],
                ['Total wholesale credit portfolio','1673579','1544441','6079','6477']]))

    def test_loan_only_totals_and_no_zero_substitution(self):
        row=f.jpm_credit_ratio(self.fixture(),'2026-06-30','filing','2026-08-06','10-Q')
        self.assertAlmostEqual(row['val'],9364/1542462*100)
        self.assertEqual(row['portfolios']['Consumer credit portfolio']['loansMillions'],641619)
        self.assertIn('definition',row)

    def test_definition_dates_units_partial_and_conflicting_totals_rejected(self):
        for old,new in [('Nonperforming assets include','Unverified definition'),('Jun 30,2026','Mar 31,2026'),('(in millions)','(in thousands)'),('249876','NA'),('Wholesale credit portfolio','Subsidiary credit portfolio'),('4916','9999999')]:
            with self.subTest(old=old):
                self.assertIsNone(f.jpm_credit_ratio(self.fixture().replace(old,new),'2026-06-30','filing','2026-08-06','10-Q'))
        self.assertIsNone(f.jpm_credit_ratio(self.fixture()+self.fixture().replace('4916','4917'),'2026-06-30','filing','2026-08-06','10-Q'))


class ReportedLoanRatioTests(unittest.TestCase):
    def test_explicit_npl_fact_rejects_subsidiary_and_nonaccrual_substitution(self):
        dims='<xbrli:segment><xbrldi:explicitMember dimension="us-gaap:FinancialInstrumentPerformanceStatusAxis">us-gaap:NonperformingFinancingReceivableMember</xbrldi:explicitMember></xbrli:segment>'
        tag='us-gaap:LoansAndLeasesReceivableRatioOfNonperformingLoansToAllLoans'
        text=document(value='0.0055',dimension=dims,currency='pure').replace('us-gaap:DebtCurrent',tag).replace('scale="6"','scale="0"')
        def run(text):
            p=f.InlineFacts();p.feed(text);p.validated(70858,'2026-06-30','issuer','2026-08-01','10-Q')
            return f.bank_loan_ratio(p,text,70858,'2026-06-30','issuer','2026-08-01','10-Q')
        self.assertAlmostEqual(run(text)['val'],.55)
        self.assertIsNone(run(text.replace('NonperformingFinancingReceivableMember','CommercialPortfolioSegmentMember')))
        self.assertIsNone(run(text.replace(tag,'custom:NonaccrualRatio')))

    def test_wfc_definition_totals_date_and_loan_only_ratio(self):
        rows=[['($ in millions)','Jun 30, 2026','Dec 31, 2025'],['Commercial loans','4735','5266'],['Consumer loans','2908','2935'],['Total nonaccrual loans','7643','8201'],['Nonaccrual loans as a % of total loans','0.74','%','0.83']]
        text='NONPERFORMING ASSETS (NONACCRUAL LOANS AND FORECLOSED ASSETS)<p>Table 11: Credit Quality Overview</p><table>'+''.join('<tr>'+''.join('<td>'+c+'</td>' for c in r)+'</tr>' for r in rows)+'</table>'
        def run(text,cik=72971):return f.bank_loan_ratio(f.InlineFacts(),text,cik,'2026-06-30','issuer','2026-07-28','10-Q')
        self.assertAlmostEqual(run(text)['val'],.74)
        for old,new in [('Jun 30, 2026','Mar 31, 2026'),('7643','8000'),('NONACCRUAL LOANS AND FORECLOSED ASSETS','unverified definition'),('Credit Quality Overview','Subsidiary summary')]:
            with self.subTest(old=old):self.assertIsNone(run(text.replace(old,new)))
        self.assertIsNone(run(text,19617))


class CitiCapitalTests(unittest.TestCase):
    def fixture(self):
        rows=[['June 30, 2026'], ['In millions of dollars, except ratios','Required Ratios','Standardized Approach','Required Ratios','Advanced Approaches'],
              ['Common Equity Tier 1 Capital','$','157,547','$','157,547'], ['Total Risk-Weighted Assets','1,232,309','1,329,505'],
              ['CET1 Capital ratio(1)','11.6','%','12.78','%','10.5','%','11.85','%']]
        return '<p>The following tables present Citigroup’s capital components and ratios as of June 30, 2026 and December 31, 2025:</p><table>'+''.join('<tr>'+''.join('<td>'+c+'</td>' for c in r)+'</tr>' for r in rows)+'</table>'

    def test_actual_parent_column_and_reconciliation(self):
        row=f.citi_capital_table(self.fixture(),'2026-06-30','filing','2026-08-01','10-Q')
        self.assertEqual(row['val'],12.78)
        self.assertEqual(row['column'],'Standardized Approach')
        self.assertEqual(row['reconciliation']['capitalMillions'],157547)

    def test_reject_subsidiary_date_minimum_wrong_columns_and_conflict(self):
        text=self.fixture()
        for old,new in [('Citigroup’s','Citibank’s'),('June 30, 2026','March 31, 2026'),('Standardized Approach','Advanced Approaches'),('CET1 Capital ratio(1)','CET1 Capital ratio requirement'),('12.78','11.6'),('157,547','100,000')]:
            with self.subTest(old=old):
                self.assertIsNone(f.citi_capital_table(text.replace(old,new),'2026-06-30','filing','2026-08-01','10-Q'))
        conflict=text+text.replace('12.78','12.00').replace('157,547','147,877')
        self.assertIsNone(f.citi_capital_table(conflict,'2026-06-30','filing','2026-08-01','10-Q'))


class CapitalContextTests(unittest.TestCase):
    def test_standardized_parent_not_subsidiary_advanced_or_minimum(self):
        dims='<xbrli:segment><xbrldi:explicitMember dimension="srt:ConsolidatedEntitiesAxis">srt:ParentCompanyMember</xbrldi:explicitMember><xbrldi:explicitMember dimension="us-gaap:RiskWeightedAssetsCalculationMethodologyAxis">us-gaap:StandardizedApproachMember</xbrldi:explicitMember></xbrli:segment>'
        text=document(value='0.1026',dimension=dims,currency='pure').replace('us-gaap:DebtCurrent','us-gaap:CommonEquityTierOneCapitalRatio').replace('scale="6"','scale="0"')
        def parse_capital(text):
            p=f.InlineFacts();p.feed(text);p.validated(70858,'2026-06-30','issuer','2026-07-31','10-Q')
            return f.capital_xbrl(p,70858,'2026-06-30','issuer','2026-07-31','10-Q')
        self.assertAlmostEqual(parse_capital(text)['val'],10.26)
        self.assertIsNone(parse_capital(text.replace('ParentCompanyMember','SubsidiariesMember')))
        self.assertIsNone(parse_capital(text.replace('StandardizedApproachMember','AdvancedApproachMember')))
        self.assertIsNone(parse_capital(text.replace('CommonEquityTierOneCapitalRatio','BankingRegulationCommonEquityTierOneRiskBasedCapitalRatioWellCapitalizedMinimum')))
        self.assertIsNone(parse_capital(text.replace('srt:ConsolidatedEntitiesAxis','dei:LegalEntityAxis')))
        self.assertEqual(parse(text),{})

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
