from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import scrape


def row(name="Film", slug="film", date="2026-10-18", runtime="90"):
    return dict(zip(scrape.FIELDNAMES, [name, "2026", "3.5", "", date,
                                       runtime, "", f"https://letterboxd.com/film/{slug}/"]))


class SyncTests(unittest.TestCase):
    def test_most_recent_diary_entry_wins(self):
        latest = {"slug": "film", "date": "2026-10-18"}
        older = {"slug": "film", "date": {"year": 2026, "month": 10, "day": 1}}
        self.assertEqual(scrape.build_diary_lookup({"entries": {"1": latest, "2": older}}),
                         {"film": latest})

    def test_rejects_empty_missing_and_duplicate_films(self):
        for rows in [[], [row(slug="other")], [row(), row()]]:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                scrape.validate_rows(rows, [row()])

    def test_rejects_lost_diary_dates(self):
        with self.assertRaisesRegex(ValueError, "watch dates"):
            scrape.validate_rows([row(date="")], [row()])

    def test_allows_new_films_and_updated_metadata(self):
        scrape.validate_rows([row(date="2026-10-28"), row(slug="new")], [row()])

    @patch.object(scrape, "UserDiary")
    @patch.object(scrape, "UserFilms")
    @patch.object(scrape, "FilmURL")
    def test_cached_runtimes_avoid_extra_requests(self, film_url, films, diary):
        films.return_value.get_films.return_value = {
            "movies": {"film": {"name": "Film", "rating": 0, "liked": True}}}
        diary.return_value.get_diary.return_value = {
            "entries": {"1": {"slug": "film", "date": "2026-10-18", "runtime": None}}}
        rows = scrape.scrape([row()])
        self.assertEqual(rows[0]["Runtime"], "90")
        self.assertEqual(rows[0]["Rating"], 0)
        self.assertEqual(rows[0]["Liked"], "Yes")
        film_url.json.assert_not_called()
        diary.return_value.get_diary.assert_called_once_with(fetch_runtime=False)

    @patch.object(scrape, "UserDiary")
    @patch.object(scrape, "UserFilms")
    @patch.object(scrape, "FilmURL")
    def test_fetches_runtime_for_new_diary_film(self, film_url, films, diary):
        films.return_value.get_films.return_value = {
            "movies": {"film": {"name": "Film", "rating": None}}}
        diary.return_value.get_diary.return_value = {
            "entries": {"1": {"slug": "film", "date": "2026-10-18", "runtime": None}}}
        film_url.json.return_value.run_time = 100
        self.assertEqual(scrape.scrape([])[0]["Runtime"], 100)
        film_url.json.assert_called_once_with("film")

    @patch.object(scrape, "UserDiary")
    @patch.object(scrape, "UserFilms")
    def test_diary_failure_is_not_silently_ignored(self, films, diary):
        films.return_value.get_films.return_value = {"movies": {}}
        diary.return_value.get_diary.side_effect = RuntimeError("Parser changed")
        with self.assertRaisesRegex(RuntimeError, "Parser changed"):
            scrape.scrape([])

    def test_retry_rotates_and_closes_sessions(self):
        sessions = [Mock(), Mock()]
        with patch.object(scrape, "ImpersonateSession", side_effect=sessions), \
             patch.object(scrape.Scraper, "set_instance"), \
             patch.object(scrape.time, "sleep"), \
             patch.object(scrape, "scrape", side_effect=[
                 scrape.AccessDeniedError(message="Blocked", url="https://letterboxd.com"),
                 [row()],
             ]):
            self.assertEqual(scrape.scrape_with_retries([]), [row()])
        for session in sessions:
            session.close.assert_called_once()

    def test_configured_proxy_is_passed_to_letterboxd_session(self):
        with patch.dict(scrape.os.environ, {"LETTERBOXD_PROXY": "http://proxy.example:8080"}), \
             patch.object(scrape, "ImpersonateSession") as session, \
             patch.object(scrape.Scraper, "set_instance"), \
             patch.object(scrape, "scrape", return_value=[row()]):
            self.assertEqual(scrape.scrape_with_retries([]), [row()])
        session.assert_called_once_with(proxy="http://proxy.example:8080")
        session.return_value.close.assert_called_once()

    def test_failed_validation_preserves_published_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "watched.csv"
            with patch.object(scrape, "OUTPUT_FILE", str(target)):
                scrape.write_rows([row()])
                original = target.read_bytes()
                with patch.object(scrape, "scrape_with_retries", return_value=[]), \
                     self.assertRaises(ValueError):
                    scrape.main()
                self.assertEqual(target.read_bytes(), original)

    def test_failed_write_preserves_csv_and_cleans_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "watched.csv"
            target.write_text("original")
            with patch.object(scrape, "OUTPUT_FILE", str(target)), \
                 self.assertRaises(ValueError):
                scrape.write_rows([{**row(), "unexpected": "invalid"}])
            self.assertEqual(target.read_text(), "original")
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_csv_round_trip_preserves_commas_unicode_and_blanks(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "watched.csv"
            with patch.object(scrape, "OUTPUT_FILE", str(target)):
                expected = row(name="Déjà Vu, again", date="", runtime="")
                scrape.write_rows([expected])
                self.assertEqual(scrape.read_previous_rows(), [expected])


if __name__ == "__main__":
    unittest.main()
