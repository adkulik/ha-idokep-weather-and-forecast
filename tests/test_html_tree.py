"""Unit tests for the page tree built with html.parser."""

from __future__ import annotations

from custom_components.idokep_weather_and_forecast.html_tree import (
    Element,
    parse_html,
)


def names(elements: list[Element]) -> list[str]:
    """Return the elements' id attributes."""
    return [element.get("id", "") for element in elements]


def test_find_by_tag_and_class() -> None:
    """A class matches one of the element's classes or its whole list."""
    page = parse_html(
        '<div id="a" class="ik  min"></div>'
        '<div id="b" class="min"></div>'
        '<span id="c" class="min ik"></span>'
        '<div id="d"></div>'
    )

    assert names(page.find_all("div", "min")) == ["a", "b"]
    assert names(page.find_all("div", "ik min")) == ["a"]
    assert names(page.find_all(class_="ik")) == ["a", "c"]
    assert names(page.find_all("div", ("ik min", "nothing"))) == ["a"]
    assert names(page.find_all("div")) == ["a", "b", "d"]
    assert page.find("div", "min").get("id") == "a"
    assert page.find("div", "max") is None
    assert page.find("div", "min").classes == ["ik", "min"]
    assert page.find_all("div")[2].classes == []


def test_attributes() -> None:
    """Values are unescaped, one without a value is empty, a missing one None."""
    link = parse_html(
        '<a data-bs-content="&lt;b&gt;sz&eacute;l&lt;/b&gt;" hidden>x</a>'
    ).find("a")

    assert link.get("data-bs-content") == "<b>szél</b>"
    assert link.get("hidden") == ""
    assert link.get("title") is None
    assert link.get("title", "-") == "-"


def test_elements_without_content() -> None:
    """Void and self-closed elements don't contain what follows them."""
    page = parse_html('<div><img src="a.svg"><br/><span/>text</br></div>')
    box = page.find("div")

    assert [node.name for node in box.children[:3]] == ["img", "br", "span"]
    assert box.children[3] == "text"
    assert box.find("img").children == []
    assert box.find("span").children == []


def test_unmatched_tags() -> None:
    """An end tag closes what is left open inside; a stray one is ignored."""
    page = parse_html("<div><p><b>one</div></i>two<p>three")

    assert page.find("div").text == "one"
    assert page.text == "onetwothree"


def test_text() -> None:
    """Entities are read; comments and scripts are not text."""
    box = parse_html(
        "<div> 8 &deg;C <!--16 mm--><script>var a = '<p>';</script>"
        "<style>p {}</style><template><i>t</i></template><b> vihar </b></div>"
    ).find("div")

    assert box.text == " 8 °C  vihar "
    assert box.get_text(" ", strip=True) == "8 °C vihar"
    assert box.comments() == ["16 mm"]
    assert box.find("p") is None


def test_find_parent() -> None:
    """The closest element around one with the tag, if there is any."""
    page = parse_html('<a id="out"><a id="in"><span><div></div></span></a></a>')
    box = page.find("div")

    assert box.find_parent("a").get("id") == "in"
    assert box.find_parent("table") is None


def test_deeply_nested_page() -> None:
    """Thousands of unclosed tags don't hit Python's recursion limit."""
    page = parse_html("<p>x" * 5000)

    assert len(page.find_all("p")) == 5000
    assert page.text == "x" * 5000
