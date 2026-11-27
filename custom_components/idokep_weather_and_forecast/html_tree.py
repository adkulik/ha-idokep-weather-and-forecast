"""
A small tree of an HTML page, built with Python's own html.parser.

Only what is needed to read the idokep.hu pages: finding elements by tag and
class, their attributes, text and comments.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

# Elements without content or end tag
VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "keygen",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
# Their content is not text of the page
HIDDEN_TEXT_TAGS = frozenset({"script", "style", "template"})


class Comment(str):
    """The text of an HTML comment."""

    __slots__ = ()


class Element:
    """An element of the page."""

    def __init__(self, name: str, attrs: dict[str, str], parent: Element | None) -> None:
        """Initialize an element without content."""
        self.name = name
        self.attrs = attrs
        self.parent = parent
        # Elements, texts and comments, in the page's order
        self.children: list[Element | str] = []

    def get(self, attribute: str, default: str | None = None) -> str | None:
        """Return an attribute's value."""
        return self.attrs.get(attribute, default)

    @property
    def classes(self) -> list[str]:
        """Return the element's classes."""
        return self.attrs.get("class", "").split()

    def _descendants(self) -> Iterator[Element | str]:
        """Yield everything inside the element, in the page's order."""
        # Not recursive: unclosed tags can nest a page very deeply
        waiting = self.children[::-1]
        while waiting:
            node = waiting.pop()
            yield node
            if isinstance(node, Element):
                waiting.extend(node.children[::-1])

    def matches(self, name: str | None, classes: tuple[str, ...]) -> bool:
        """Return True if the element has the tag and one of the classes."""
        if name is not None and self.name != name:
            return False
        if not classes:
            return True
        own = self.classes
        # One of its classes, or the whole list as written ("ik min")
        return any(wanted in own or wanted == " ".join(own) for wanted in classes)

    def _find(
        self, name: str | None, class_: str | tuple[str, ...]
    ) -> Iterator[Element]:
        """Yield the matching elements inside this one."""
        classes = (class_,) if isinstance(class_, str) else class_
        for node in self._descendants():
            if isinstance(node, Element) and node.matches(name, classes):
                yield node

    def find(
        self, name: str | None = None, class_: str | tuple[str, ...] = ()
    ) -> Element | None:
        """Return the first element inside with the tag and one of the classes."""
        return next(self._find(name, class_), None)

    def find_all(
        self, name: str | None = None, class_: str | tuple[str, ...] = ()
    ) -> list[Element]:
        """Return every element inside with the tag and one of the classes."""
        return list(self._find(name, class_))

    def find_parent(self, name: str) -> Element | None:
        """Return the closest element around this one with the tag."""
        parent = self.parent
        while parent is not None and parent.name != name:
            parent = parent.parent
        return parent

    def comments(self) -> list[Comment]:
        """Return the comments inside the element."""
        return [node for node in self._descendants() if isinstance(node, Comment)]

    def get_text(self, separator: str = "", *, strip: bool = False) -> str:
        """Return the texts inside; strip leaves out spaces and empty texts."""
        texts = (
            node
            for node in self._descendants()
            if isinstance(node, str) and not isinstance(node, Comment)
        )
        if strip:
            texts = (stripped for text in texts if (stripped := text.strip()))
        return separator.join(texts)

    @property
    def text(self) -> str:
        """Return the element's text as it is on the page."""
        return self.get_text()


class _TreeBuilder(HTMLParser):
    """Build the tree while html.parser reads the page."""

    def __init__(self) -> None:
        """Start an empty page."""
        super().__init__(convert_charrefs=True)
        self.root = Element("[document]", {}, None)
        # The elements not closed yet, the page itself first
        self._open = [self.root]
        # The pieces of the text being read
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Add an element; html.parser ends a "<tag/>" right after this."""
        self._add_text()
        element = Element(
            tag, {name: value or "" for name, value in attrs}, self._open[-1]
        )
        self._open[-1].children.append(element)
        if tag not in VOID_TAGS:
            self._open.append(element)

    def handle_endtag(self, tag: str) -> None:
        """Close the last open element of the tag, and those left open in it."""
        self._add_text()
        # An end tag without an open element is ignored
        for index in range(len(self._open) - 1, 0, -1):
            if self._open[index].name == tag:
                del self._open[index:]
                return

    def handle_data(self, data: str) -> None:
        """Collect a piece of text."""
        self._text.append(data)

    def handle_comment(self, data: str) -> None:
        """Add a comment."""
        self._add_text()
        self._open[-1].children.append(Comment(data))

    def close(self) -> None:
        """Read what is left of the page."""
        super().close()
        self._add_text()

    def _add_text(self) -> None:
        """Add the collected text to the element it is in."""
        text = "".join(self._text)
        self._text.clear()
        if text and not any(item.name in HIDDEN_TEXT_TAGS for item in self._open):
            self._open[-1].children.append(text)


def parse_html(page: str) -> Element:
    """Return the page's tree."""
    builder = _TreeBuilder()
    builder.feed(page)
    builder.close()
    return builder.root
