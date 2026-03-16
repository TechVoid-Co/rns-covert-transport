"""Tests for email camouflage locales."""

import pytest
from rns_covert.locale import get_locale, RussianLocale, EnglishLocale, NeutralLocale


class TestRussianLocale:
    def test_subjects_varied(self):
        subjects = set(RussianLocale.generate_subject() for _ in range(50))
        assert len(subjects) > 15

    def test_subjects_have_cyrillic(self):
        for _ in range(20):
            subj = RussianLocale.generate_subject()
            assert any('\u0400' <= ch <= '\u04ff' for ch in subj), subj

    def test_filenames_varied(self):
        filenames = set(RussianLocale.generate_filename() for _ in range(50))
        assert len(filenames) > 10

    def test_filenames_have_extensions(self):
        for _ in range(30):
            assert "." in RussianLocale.generate_filename()

    def test_body_with_attachment(self):
        body = RussianLocale.generate_body(has_attachment=True)
        assert body is not None
        assert any('\u0400' <= ch <= '\u04ff' for ch in body)

    def test_body_without_attachment(self):
        assert RussianLocale.generate_body(has_attachment=False) is None


class TestEnglishLocale:
    def test_subjects_varied(self):
        subjects = set(EnglishLocale.generate_subject() for _ in range(50))
        assert len(subjects) > 15

    def test_subjects_are_ascii(self):
        for _ in range(20):
            EnglishLocale.generate_subject().encode("ascii")

    def test_filenames_have_extensions(self):
        for _ in range(30):
            assert "." in EnglishLocale.generate_filename()

    def test_body_with_attachment(self):
        body = EnglishLocale.generate_body(has_attachment=True)
        assert body is not None
        body.encode("ascii")

    def test_body_without_attachment(self):
        assert EnglishLocale.generate_body(has_attachment=False) is None


class TestNeutralLocale:
    def test_subjects_ascii(self):
        for _ in range(20):
            NeutralLocale.generate_subject().encode("ascii")

    def test_filenames_ascii(self):
        for _ in range(20):
            NeutralLocale.generate_filename().encode("ascii")

    def test_body_short(self):
        body = NeutralLocale.generate_body(has_attachment=True)
        assert body is not None
        assert len(body) < 50


class TestLocaleRegistry:
    def test_get_ru(self):
        assert get_locale("ru") is RussianLocale

    def test_get_en(self):
        assert get_locale("en") is EnglishLocale

    def test_get_neutral(self):
        assert get_locale("neutral") is NeutralLocale

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown locale"):
            get_locale("klingon")
