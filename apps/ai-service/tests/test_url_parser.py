import unittest
from src.transcription.utils import parse_google_meet_url
from src.transcription.exceptions import InvalidMeetingUrlError


class TestGoogleMeetUrlParser(unittest.TestCase):

    def test_standard_https_url(self):
        url = "https://meet.google.com/abc-defg-hij"
        self.assertEqual(parse_google_meet_url(url), "abc-defg-hij")

    def test_http_url(self):
        url = "http://meet.google.com/abc-defg-hij"
        self.assertEqual(parse_google_meet_url(url), "abc-defg-hij")

    def test_url_without_scheme(self):
        url = "meet.google.com/abc-defg-hij"
        self.assertEqual(parse_google_meet_url(url), "abc-defg-hij")

    def test_url_with_query_parameters(self):
        url = "https://meet.google.com/abc-defg-hij?authuser=0&hl=en"
        self.assertEqual(parse_google_meet_url(url), "abc-defg-hij")

    def test_url_with_trailing_slash(self):
        url = "https://meet.google.com/abc-defg-hij/"
        self.assertEqual(parse_google_meet_url(url), "abc-defg-hij")

    def test_direct_hyphenated_code(self):
        code = "abc-defg-hij"
        self.assertEqual(parse_google_meet_url(code), "abc-defg-hij")

    def test_case_insensitivity(self):
        url = "https://meet.google.com/ABC-DEFG-HIJ"
        self.assertEqual(parse_google_meet_url(url), "abc-defg-hij")

    def test_unhyphenated_10_char_code(self):
        code = "abcdefghij"
        self.assertEqual(parse_google_meet_url(code), "abc-defg-hij")

    def test_invalid_domains_raise_error(self):
        invalid_urls = [
            "https://zoom.us/j/123456789",
            "https://teams.microsoft.com/l/meetup-join/123",
            "https://google.com/abc-defg-hij",
            "https://notgoogle.meet.com/abc-defg-hij"
        ]
        for url in invalid_urls:
            with self.subTest(url=url):
                with self.assertRaises(InvalidMeetingUrlError):
                    parse_google_meet_url(url)

    def test_empty_or_malformed_urls_raise_error(self):
        malformed = [
            "",
            "   ",
            "https://meet.google.com/",
            "https://meet.google.com/short",
            "https://meet.google.com/123-4567-890",  # non-alphabetical
            "random_string_not_a_url"
        ]
        for item in malformed:
            with self.subTest(item=item):
                with self.assertRaises(InvalidMeetingUrlError):
                    parse_google_meet_url(item)


if __name__ == "__main__":
    unittest.main()
