"""meta.yaml 校验器测试（M0.4 验收）。

验收标准是「故意漏填 youtube.title 时能报出准确的行号与字段名」，
所以这里对行号做了硬断言。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vidrelay.schema import (
    MetaValidationError,
    build_line_index,
    load_and_validate,
    validate_file,
    validate_text,
)

FIXTURES = Path(__file__).parent / "fixtures"
VALID = FIXTURES / "valid.yaml"
INVALID = FIXTURES / "invalid.yaml"


def _errors_by_path(result) -> dict[str, object]:
    return {i.path: i for i in result.errors}


class TestLineIndex:
    def test_maps_nested_paths(self) -> None:
        idx = build_line_index((FIXTURES / "invalid.yaml").read_text(encoding="utf-8"))
        assert idx["video"] == 5
        assert idx["platforms"] == 10
        assert idx["platforms.bilibili"] == 16
        assert idx["platforms.youtube"] == 20
        assert idx["platforms.youtube.privacy"] == 23
        assert idx["platforms.dailymotion.typo_field"] == 30

    def test_skips_comments_and_blanks(self) -> None:
        idx = build_line_index("# 注释\n\nkey:\n  child: 1\n")
        assert idx == {"key": 3, "key.child": 4}


class TestValidFixture:
    def test_passes(self) -> None:
        result = validate_file(VALID)
        assert result.ok, [i.render() for i in result.issues]
        assert result.errors == []

    def test_load_and_validate_returns_data(self) -> None:
        data = load_and_validate(VALID)
        assert set(data["platforms"]) == {
            "douyin",
            "kuaishou",
            "bilibili",
            "tiktok",
            "youtube",
            "dailymotion",
        }


@pytest.fixture(scope="module")
def result():
    return validate_file(INVALID)


class TestInvalidFixture:
    def test_not_ok(self, result) -> None:
        assert not result.ok

    def test_missing_title_has_path_and_line(self, result) -> None:
        errs = _errors_by_path(result)
        assert "platforms.youtube.title" in errs
        issue = errs["platforms.youtube.title"]
        assert "必填" in issue.message
        # youtube 块从第 20 行开始，title 缺失 → 回退到块首行
        assert issue.line == 20
        assert f"invalid.yaml:{issue.line}" in issue.render("invalid.yaml")

    def test_missing_tid_reported(self, result) -> None:
        errs = _errors_by_path(result)
        assert "platforms.bilibili.tid" in errs
        assert errs["platforms.bilibili.tid"].line == 16

    def test_unknown_platform_reported(self, result) -> None:
        errs = _errors_by_path(result)
        assert "platforms.weibo" in errs
        assert "未知平台" in errs["platforms.weibo"].message
        assert errs["platforms.weibo"].line == 25

    def test_unknown_field_reported_with_exact_line(self, result) -> None:
        errs = _errors_by_path(result)
        assert "platforms.dailymotion.typo_field" in errs
        assert errs["platforms.dailymotion.typo_field"].line == 30

    def test_bad_enum_reported(self, result) -> None:
        errs = _errors_by_path(result)
        assert "platforms.youtube.privacy" in errs
        assert "取值必须是" in errs["platforms.youtube.privacy"].message

    def test_publish_at_without_timezone_is_warning(self, result) -> None:
        warns = {i.path for i in result.warnings}
        assert "common.publish_at" in warns
        assert not any(i.path == "common.publish_at" for i in result.errors)

    def test_load_and_validate_raises_with_rendered_body(self) -> None:
        with pytest.raises(MetaValidationError) as exc:
            load_and_validate(INVALID)
        text = str(exc.value)
        assert "platforms.youtube.title" in text
        assert "invalid.yaml:20" in text


class TestEdgeCases:
    def test_empty_file(self) -> None:
        r = validate_text("")
        assert not r.ok
        assert "空" in r.issues[0].message

    def test_yaml_syntax_error_reports_line(self) -> None:
        r = validate_text("video: x\nplatforms:\n  - 这里不该是列表\n   bad indent: 1\n")
        assert not r.ok
        assert r.issues[0].line is not None

    def test_missing_video(self) -> None:
        r = validate_text("platforms:\n  youtube:\n    title: t\n")
        assert any(i.path == "video" for i in r.errors)

    def test_missing_platforms(self) -> None:
        r = validate_text('video: "a.mp4"\n')
        assert any(i.path == "platforms" for i in r.errors)

    def test_unknown_top_level_field(self) -> None:
        r = validate_text('video: "a.mp4"\nplatforms:\n  youtube:\n    title: t\nbogus: 1\n')
        assert any(i.path == "bogus" for i in r.errors)

    def test_wrong_type_for_tags(self) -> None:
        r = validate_text('video: "a.mp4"\nplatforms:\n  youtube:\n    title: t\n    tags: "不是列表"\n')
        assert any(i.path == "platforms.youtube.tags" for i in r.errors)

    def test_tags_items_must_be_strings(self) -> None:
        r = validate_text('video: "a.mp4"\nplatforms:\n  youtube:\n    title: t\n    tags: [1, 2]\n')
        assert any("每一项都必须是字符串" in i.message for i in r.errors)

    def test_youtube_short_without_hashtag_warns(self) -> None:
        r = validate_text(
            'video: "a.mp4"\nplatforms:\n  youtube:\n    title: t\n    short: true\n'
        )
        assert r.ok
        assert any("#Shorts" in i.message for i in r.warnings)

    def test_dailymotion_chinese_title_warns(self) -> None:
        r = validate_text('video: "a.mp4"\nplatforms:\n  dailymotion:\n    title: 中文标题\n')
        assert r.ok
        assert any(i.path == "platforms.dailymotion.title" for i in r.warnings)

    def test_non_mapping_root(self) -> None:
        r = validate_text("- a\n- b\n")
        assert not r.ok
        assert "顶层必须是一个映射" in r.issues[0].message

    def test_missing_file(self) -> None:
        r = validate_file(FIXTURES / "does-not-exist.yaml")
        assert not r.ok
        assert "文件不存在" in r.issues[0].message
