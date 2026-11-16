"""The scorer's location rule: a real US market must not be rejected, a foreign or artifact location must not pass.

Pure functions only; no database, browser or network.
Run: python3 -m unittest discover -s test -p 'test_score_location.py'
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import score

# united_states mode: on-site or hybrid anywhere in the US is acceptable because she is willing to relocate.
PREFS = {'location_mode': 'united_states', 'accept_remote': True}


def accepted(location, remote=0):
    return score.location_ok({'location': location, 'remote': remote, 'description': ''}, PREFS)


class UsLocationTests(unittest.TestCase):
    def test_linkedin_metro_area_wording_for_us_markets_is_accepted(self):
        # LinkedIn drops the state code for metros, which previously rejected even her home market.
        for location in ('Greater Boston', 'Greater Boston (Hybrid)', 'Greater Boston (On-site)',
                         'Greater Chicago Area (On-site)', 'Greater Houston (On-site)',
                         'Greater Philadelphia (Hybrid)', 'New York City Metropolitan Area (Hybrid)',
                         'San Francisco Bay Area (On-site)', 'Dallas-Fort Worth Metroplex (Hybrid)',
                         'Washington DC-Baltimore Area (On-site)', 'Los Angeles Metropolitan Area (On-site)',
                         'Columbia, South Carolina Metropolitan Area (On-site)'):
            with self.subTest(location=location):
                self.assertTrue(accepted(location))

    def test_metro_wording_for_foreign_cities_is_rejected(self):
        # The same shape as the US metros above, so a pattern would accept all of these.
        for location in ('Greater London Area', 'Greater Toronto Area', 'Greater Mumbai Area',
                         'Bengaluru Metropolitan Area', 'Greater Sydney Area', 'Greater Paris Metropolitan Area',
                         'Greater Dublin Area', 'Greater Vancouver Area', 'Greater Manchester Area',
                         'Mexico City Metropolitan Area', 'Greater Boston, United Kingdom'):
            with self.subTest(location=location):
                self.assertFalse(accepted(location))

    def test_a_company_name_in_the_location_field_is_never_a_place(self):
        # A scraping artifact: the company landed in the location column.
        for location in ("Boston Children's Hospital", 'Acme Health', 'Mass General Brigham',
                         'Beth Israel Lahey Health', 'Northwestern Medicine', 'Tempus AI', 'Atos', ''):
            with self.subTest(location=location):
                self.assertFalse(accepted(location))

    def test_locations_that_already_worked_still_work(self):
        for location in ('Boston, MA', 'Boston, MA (Hybrid)', 'Cambridge, Massachusetts, United States',
                         'Austin, TX', 'United States'):
            with self.subTest(location=location):
                self.assertTrue(accepted(location))

    def test_an_explicit_foreign_country_still_overrides_a_us_looking_metro(self):
        for location in ('Toronto, Canada', 'London, United Kingdom', 'Bengaluru, India'):
            with self.subTest(location=location):
                self.assertFalse(accepted(location))


class StateTaggedRemoteTests(unittest.TestCase):
    """Known US remote tags pass screening; explicit residency exclusions still win."""

    @staticmethod
    def remote(description, location='Raritan, NJ (Remote)'):
        return score.location_ok({'location': location, 'remote': 1, 'description': description}, PREFS, 'MA')

    def test_explicit_national_or_massachusetts_scope_qualifies(self):
        for text in ('Fully remote. Open to candidates anywhere in the United States.',
                     'Remote across the United States. We hire in all 50 states.',
                     'This role can be performed remotely from anywhere in the US.',
                     'Remote role. Eligible states: MA, NJ, NY.'):
            with self.subTest(text=text):
                self.assertTrue(self.remote(text))

    def test_a_known_us_remote_tag_without_a_scope_statement_can_enter_screening(self):
        for text in ('', 'Great benefits. Join our team.', 'Competitive salary and a strong team culture.'):
            with self.subTest(text=text):
                self.assertTrue(self.remote(text))

    def test_unknown_and_foreign_remote_locations_are_not_assumed_us_eligible(self):
        for location in ('Remote', 'Toronto, Canada (Remote)', 'London, United Kingdom (Remote)'):
            with self.subTest(location=location):
                self.assertFalse(self.remote('', location=location))

    def test_a_scope_statement_never_overrides_an_explicit_restriction(self):
        for text in ('We cannot hire in Massachusetts.',
                     'Remote across the United States. We cannot hire in Massachusetts.',
                     'Remote across the United States, but candidates must reside in NJ, NY or PA.',
                     'Candidates must reside in New Jersey or New York.',
                     'Eligible states: NJ, NY, PA.',
                     'Remote in Canada only.'):
            with self.subTest(text=text):
                self.assertFalse(self.remote(text))

    def test_locations_that_were_already_eligible_stay_eligible_without_a_description(self):
        self.assertTrue(self.remote('', location='United States (Remote)'))
        self.assertTrue(self.remote('', location='Boston, MA (Remote)'))

    def test_a_negated_exclusion_means_the_opposite_and_stays_eligible(self):
        # "Massachusetts not excluded" used to match the exclusion pattern and reject a job that says the reverse.
        for text in ('US remote excluding listed states; Massachusetts not excluded.',
                     'Remote across the United States. Massachusetts is not excluded from this role.',
                     "Remote across the United States. Massachusetts isn't excluded.",
                     'Remote across the United States. Massachusetts is never ineligible for remote work.',
                     'Remote across the United States. Massachusetts is not ineligible.',
                     'Remote across the United States. Massachusetts and New York are not excluded.'):
            with self.subTest(text=text):
                self.assertTrue(self.remote(text, location='United States (Remote)'))

    def test_a_genuine_exclusion_of_massachusetts_still_rejects_in_every_phrasing(self):
        for text in ('Remote across the United States. Residents of Massachusetts are excluded.',
                     'Remote across the United States. Massachusetts is excluded.',
                     'Remote across the United States. Massachusetts candidates are ineligible.',
                     'Remote across the United States. Massachusetts is not eligible for this role.',
                     'Remote across the United States. MA residents not eligible.',
                     'Remote across the United States. Applicants in MA, NY and CA are excluded.'):
            with self.subTest(text=text):
                self.assertFalse(self.remote(text, location='United States (Remote)'))


class HomeStateComesFromTheProfileTests(unittest.TestCase):
    """The residency rule applies to the candidate's own state, whatever it is."""

    @staticmethod
    def remote(description, home):
        return score.location_ok({'location': 'United States (Remote)', 'remote': 1, 'description': description}, PREFS, home)

    def test_an_exclusion_applies_only_to_the_candidates_own_state(self):
        text = 'Remote across the United States. We cannot hire in Texas.'
        self.assertFalse(self.remote(text, 'TX'))
        self.assertTrue(self.remote(text, 'MA'))

    def test_an_eligible_state_list_must_include_the_candidates_state(self):
        text = 'Remote role. Eligible states: OH, PA, NY.'
        self.assertTrue(self.remote(text, 'OH'))
        self.assertFalse(self.remote(text, 'CA'))

    def test_an_unknown_home_state_cannot_pass_a_state_list(self):
        self.assertFalse(self.remote('Remote role. Eligible states: OH, PA, NY.', None))

    def test_a_time_zone_helps_only_residents_of_that_zone(self):
        text = 'Must reside in the Eastern time zone.'
        self.assertTrue(self.remote(text, 'NY'))
        self.assertFalse(self.remote(text, 'CA'))

    def test_home_state_is_read_from_candidate_state_or_the_end_of_location(self):
        self.assertEqual(score.home_state({'candidate': {'state': 'tx'}}), 'TX')
        self.assertEqual(score.home_state({'candidate': {'location': 'Austin, TX'}}), 'TX')
        self.assertIsNone(score.home_state({'candidate': {'location': 'Greater Boston Area'}}))
        self.assertIsNone(score.home_state({}))

    def test_full_state_names_in_a_city_list_match_their_abbreviations(self):
        prefs = {'location_mode': 'cities', 'locations': ['Austin, TX'], 'accept_remote': False}
        self.assertTrue(score.location_ok({'location': 'Austin, Texas', 'remote': 0, 'description': ''}, prefs))
        self.assertFalse(score.location_ok({'location': 'Austin, Ohio', 'remote': 0, 'description': ''}, prefs))


if __name__ == '__main__':
    unittest.main()
