# vim:fileencoding=UTF-8:ts=4:sw=4:sta:et:sts=4:ai

"""Output processing for KePub files."""

__license__ = "GPL v3"
__copyright__ = "2013, Joel Goguen <jgoguen@jgoguen.ca>"
__docformat__ = "markdown en"

import json
import os
from datetime import datetime
from typing import Any
from typing import Set
from typing import Tuple

from calibre.constants import config_dir
from calibre.customize.conversion import OptionRecommendation
from calibre.customize.conversion import OutputFormatPlugin
from calibre.ebooks.conversion.plugins.epub_output import EPUBOutput
from calibre.ebooks.metadata.book.base import Metadata
from calibre.ebooks.metadata.book.base import NULL_VALUES

from calibre_plugins.kepubout import common
from calibre_plugins.kepubout.container import KEPubContainer


# Support load_translations() without forcing calibre 1.9+
try:
    load_translations()
except NameError:
    pass


class KEPubOutput(OutputFormatPlugin):
    """Allows calibre to convert any known source format to a KePub file."""

    name = "KePub Output"
    author = "Joel Goguen"
    file_type = "kepub"
    version = common.PLUGIN_VERSION
    minimum_calibre_version = common.PLUGIN_MINIMUM_CALIBRE_VERSION

    epub_output_plugin = None
    configdir = os.path.join(config_dir, "plugins")
    reference_kepub = os.path.join(configdir, "reference.kepub.epub")
    kepub_options: Set[OptionRecommendation] = {
        OptionRecommendation(
            name="kepub_hyphenate",
            recommended_value=True,
            help=" ".join(
                [
                    _(  # noqa: F821
                        "Select this to add a CSS file which enables hyphenation."
                    ),
                    _(  # noqa: F821
                        "The language used will be the language defined for the "
                        + "book in calibre."
                    ),
                    _(  # noqa: F821
                        "Please see the README file for directions on updating "
                        + "hyphenation dictionaries."
                    ),
                ]
            ),
        ),
        OptionRecommendation(
            name="kepub_disable_hyphenation",
            recommended_value=False,
            help=" ".join(
                [
                    _(  # noqa: F821
                        "Select this to disable all hyphenation in a book."
                    ),
                    _(  # noqa: F821
                        "This takes precedence over the hyphenation option."
                    ),
                ]
            ),
        ),
        OptionRecommendation(
            name="kepub_clean_markup",
            recommended_value=True,
            help=_("Select this to clean up the internal ePub markup."),  # noqa: F821
        ),
        OptionRecommendation(
            name="kepub_hyphenate_chars",
            recommended_value=6,
            help=_(  # noqa: F821
                "Sets the minimum word length, in characters, for hyphenation "
                + "to be allowed."
            ),
        ),
        OptionRecommendation(
            name="kepub_hyphenate_chars_before",
            recommended_value=3,
            help=_(  # noqa: F821
                "Sets the minimum number of characters which must appear before "
                + "a hyphen"
            ),
        ),
        OptionRecommendation(
            name="kepub_hyphenate_chars_after",
            recommended_value=3,
            help=_(  # noqa: F821
                "Sets the minimum number of characters which must appear after a hyphen"
            ),
        ),
        OptionRecommendation(
            name="kepub_hyphenate_limit_lines",
            recommended_value=2,
            help=" ".join(
                [
                    _(  # noqa: F821
                        "Sets the maximum number of consecutive lines that may be "
                        + "hyphenated."
                    ),
                    _("Set this to 0 to disable limiting."),  # noqa: F821
                ]
            ),
        ),
    }
    kepub_recommendations: Set[Tuple[str, Any, int]] = {
        ("epub_version", "3", OptionRecommendation.LOW)
    }

    def __init__(self, *args, **kwargs):
        """Initialize the KePub output converter."""
        self.epub_output_plugin = EPUBOutput(*args, **kwargs)
        self.options = self.epub_output_plugin.options.union(self.kepub_options)
        self.recommendations: Set[
            Tuple[str, Any, int]
        ] = self.epub_output_plugin.recommendations.union(self.kepub_recommendations)
        OutputFormatPlugin.__init__(self, *args, **kwargs)

    def gui_configuration_widget(
        self, parent, get_option_by_name, get_option_help, db, book_id=None
    ):
        """Set up the plugin configuration widget."""
        from calibre_plugins.kepubout.conversion.output_config import PluginWidget

        return PluginWidget(parent, get_option_by_name, get_option_help, db, book_id)

    def convert(self, oeb_book, output, input_plugin, opts, logger):
        """Convert from calibre's internal format to KePub."""
        common.log.debug("Running ePub conversion")
        self.epub_output_plugin.convert(
            oeb_book, output, input_plugin, opts, common.log
        )
        common.log.debug("Done ePub conversion")
        container = KEPubContainer(output, common.log, opts.kepub_clean_markup)

        if container.is_drm_encumbered:
            common.log.error("DRM-encumbered container, skipping conversion")
            return

        # Write the details file
        o = {
            "kepub_output_version": ".".join([str(n) for n in self.version]),
            "kepub_output_currenttime": datetime.utcnow().ctime(),
        }
        kte_data_file = self.temporary_file("_KePubOutputPluginInfo")
        kte_data_file.write(json.dumps(o).encode("UTF-8"))
        kte_data_file.close()
        container.copy_file_to_container(
            kte_data_file.name, name="plugininfo.kte", mt="application/json"
        )

        title = container.opf_xpath("./opf:metadata/dc:title/text()")
        if len(title) > 0:
            title = title[0]
        else:
            title = NULL_VALUES["title"]
        authors = container.opf_xpath(
            './opf:metadata/dc:creator[@opf:role="aut"]/text()'
        )
        if len(authors) < 1:
            authors = NULL_VALUES["authors"]
        mi = Metadata(title, authors)
        language = container.opf_xpath("./opf:metadata/dc:language/text()")
        if len(language) > 0:
            mi.languages = language
            language = language[0]
        else:
            mi.languages = NULL_VALUES["languages"]
            language = NULL_VALUES["language"]

        try:
            common.modify_epub(
                container,
                output,
                metadata=mi,
                opts={
                    "hyphenate": opts.kepub_hyphenate,
                    "hyphen_min_chars": opts.kepub_hyphenate_chars,
                    "hyphen_min_chars_before": opts.kepub_hyphenate_chars_before,
                    "hyphen_min_chars_after": opts.kepub_hyphenate_chars_after,
                    "hyphen_limit_lines": opts.kepub_hyphenate_limit_lines,
                    "no-hyphens": opts.kepub_disable_hyphenation,
                    "smarten_punctuation": False,
                    "extended_kepub_features": True,
                },
            )
        except Exception:
            common.log.exception("Failed converting!")
            raise
