#! /usr/bin/env python
# -*- coding: iso-8859-1 -*-
# Originally written by Barry Warsaw <barry@zope.com>
#
# Minimally patched to make it even more xgettext compatible
# by Peter Funk <pf@artcom-gmbh.de>
#
# 2002-11-22 J�rgen Hermann <jh@web.de>
# Added checks that _() only contains string literals, and
# command line args are resolved to module lists, i.e. you
# can now pass a filename, a module or package name, or a
# directory (including globbing chars, important for Win32).
# Made docstring fit in 80 chars wide displays using pydoc.
#

"""Generates gettext templates."""

import getopt
import importlib.machinery
import operator
import os
import sys
import time
import token
import tokenize
from functools import reduce
from typing import List

# for selftesting
try:
    import fintl

    _ = fintl.gettext
except ImportError:

    def _(s):
        return s


__doc__ = _(
    """pygettext -- Python equivalent of xgettext(1)

Many systems (Solaris, Linux, Gnu) provide extensive tools that ease the
internationalization of C programs. Most of these tools are independent of
the programming language and can be used from within Python programs.
Martin von Loewis' work[1] helps considerably in this regard.

There's one problem though; xgettext is the program that scans source code
looking for message strings, but it groks only C (or C++). Python
introduces a few wrinkles, such as dual quoting characters, triple quoted
strings, and raw strings. xgettext understands none of this.

Enter pygettext, which uses Python's standard tokenize module to scan
Python source code, generating .pot files identical to what GNU xgettext[2]
generates for C and C++ code. From there, the standard GNU tools can be
used.

A word about marking Python strings as candidates for translation. GNU
xgettext recognizes the following keywords: gettext, dgettext, dcgettext,
and gettext_noop. But those can be a lot of text to include all over your
code. C and C++ have a trick: they use the C preprocessor. Most
internationalized C source includes a #define for gettext() to _() so that
what has to be written in the source is much less. Thus these are both
translatable strings:

    gettext("Translatable String")
    _("Translatable String")

Python of course has no preprocessor so this doesn't work so well.  Thus,
pygettext searches only for _() by default, but see the -k/--keyword flag
below for how to augment this.

 [1] http://www.python.org/workshops/1997-10/proceedings/loewis.html
 [2] http://www.gnu.org/software/gettext/gettext.html

NOTE: pygettext attempts to be option and feature compatible with GNU
xgettext where ever possible. However some options are still missing or are
not fully implemented. Also, xgettext's use of command line switches with
option arguments is broken, and in these cases, pygettext just defines
additional switches.

Usage: pygettext [options] inputfile ...

Options:

    -a
    --extract-all
        Extract all strings.

    -d name
    --default-domain=name
        Rename the default output file from messages.pot to name.pot.

    -E
    --escape
        Replace non-ASCII characters with octal escape sequences.

    -D
    --docstrings
        Extract module, class, method, and function docstrings.  These do
        not need to be wrapped in _() markers, and in fact cannot be for
        Python to consider them docstrings. (See also the -X option).

    -h
    --help
        Print this help message and exit.

    -k word
    --keyword=word
        Keywords to look for in addition to the default set, which are:
        %(DEFAULTKEYWORDS)s

        You can have multiple -k flags on the command line.

    -K
    --no-default-keywords
        Disable the default set of keywords (see above).  Any keywords
        explicitly added with the -k/--keyword option are still recognized.

    --no-location
        Do not write filename/lineno location comments.

    -n
    --add-location
        Write filename/lineno location comments indicating where each
        extracted string is found in the source.  These lines appear before
        each msgid.  The style of comments is controlled by the -S/--style
        option.  This is the default.

    -o filename
    --output=filename
        Rename the default output file from messages.pot to filename.  If
        filename is `-' then the output is sent to standard out.

    -p dir
    --output-dir=dir
        Output files will be placed in directory dir.

    -S stylename
    --style stylename
        Specify which style to use for location comments.  Two styles are
        supported:

        Solaris  # File: filename, line: line-number
        GNU      #: filename:line

        The style name is case insensitive.  GNU style is the default.

    -v
    --verbose
        Print the names of the files being processed.

    -V
    --version
        Print the version of pygettext and exit.

    -w columns
    --width=columns
        Set width of output to columns.

    -x filename
    --exclude-file=filename
        Specify a file that contains a list of strings that are not be
        extracted from the input files.  Each string to be excluded must
        appear on a line by itself in the file.

    -X filename
    --no-docstrings=filename
        Specify a file that contains a list of files (one per line) that
        should not have their docstrings extracted.  This is only useful in
        conjunction with the -D option above.

If `inputfile' is -, standard input is read.
"""
)

__version__ = "1.5"

default_keywords = ["_"]
DEFAULTKEYWORDS = ", ".join(default_keywords)

EMPTYSTRING = ""

# The normal pot-file header. msgmerge and Emacs's po-mode work better if it's
# there.
pot_header = _(
    """\
# SOME DESCRIPTIVE TITLE.
# Copyright (C) YEAR ORGANIZATION
# FIRST AUTHOR <EMAIL@ADDRESS>, YEAR.
#
msgid ""
msgstr ""
"Project-Id-Version: PACKAGE VERSION\\n"
"POT-Creation-Date: %(time)s\\n"
"PO-Revision-Date: YEAR-MO-DA HO:MI+ZONE\\n"
"Last-Translator: FULL NAME <EMAIL@ADDRESS>\\n"
"Language-Team: LANGUAGE <LL@li.org>\\n"
"MIME-Version: 1.0\\n"
"Content-Type: text/plain; charset=CHARSET\\n"
"Content-Transfer-Encoding: ENCODING\\n"
"Generated-By: pygettext.py %(version)s\\n"

"""
)


def usage(code, msg=""):
    """Print script usage."""
    print(__doc__ % globals(), file=sys.stderr)
    if msg:
        print(msg, file=sys.stderr)
    sys.exit(code)


escapes = []


def make_escapes(pass_iso8859):
    """Escape any non-ASCII character."""
    if pass_iso8859:
        # Allow iso-8859 characters to pass through so that e.g. 'msgid
        # "H�he"' would result not result in 'msgid "H\366he"'.  Otherwise we
        # escape any character outside the 32..126 range.
        mod = 128
    else:
        mod = 256
    for i in range(256):
        if 32 <= (i % mod) <= 126:
            escapes.append(chr(i))
        else:
            escapes.append("\\%03o" % i)
    escapes[ord("\\")] = "\\\\"
    escapes[ord("\t")] = "\\t"
    escapes[ord("\r")] = "\\r"
    escapes[ord("\n")] = "\\n"
    escapes[ord('"')] = '\\"'


def escape(s):
    """Process escapes for a string."""
    s = list(s)
    for i in range(len(s)):
        s[i] = escapes[ord(s[i])]
    return EMPTYSTRING.join(s)


def safe_eval(s):
    """Unwrap quotes, safely."""
    return eval(s, {"__builtins__": {}}, {})


def normalize(s):
    """Convert Python string types into a format appropriate for .po files."""
    lines = s.split("\n")
    if len(lines) == 1:
        s = '"' + escape(s) + '"'
    else:
        if not lines[-1]:
            del lines[-1]
            lines[-1] = lines[-1] + "\n"
        for i in range(len(lines)):
            lines[i] = escape(lines[i])
        lineterm = '\\n"\n"'
        s = '""\n"' + lineterm.join(lines) + '"'
    return s


def contains_any(haystack, needles):
    """Check whether 'haystack' contains ANY of the chars in 'needles'."""
    return 1 in [c in haystack for c in needles]


def get_files_for_name(name):
    """Get a list of module files.

    Fetches a list of module files for a filename, a module or package name, or
    a directory.
    """
    if os.path.isdir(name):
        # find all python files in directory
        file_list = []
        for root, _, files in os.walk(name):
            for fname in files:
                if os.path.splitext(fname)[-1] in importlib.machinery.SOURCE_SUFFIXES:
                    file_list.append(os.path.join(root, fname))
        return file_list

    if (
        os.path.exists(name)
        and os.path.splitext(name)[-1] in importlib.machinery.SOURCE_SUFFIXES
    ):
        # a single file
        return [name]

    return []


class TokenEater:
    """Likes to eat tokens."""

    def __init__(self, options):
        """Initialize a TokenEater."""
        self.__options = options
        self.__messages = {}
        self.__state = self.__waiting
        self.__data = []
        self.__lineno = -1
        self.__freshmodule = 1
        self.__curfile = None

    def __call__(self, ttype, tstring, stup, etup, line):
        """Allow this class to be called like a function."""
        self.__state(ttype, tstring, line)

    def __waiting(self, ttype, tstring, lineno):
        """Get the waiting state."""
        opts = self.__options
        # Do docstring extractions, if enabled
        if opts.docstrings and not opts.nodocstrings.get(self.__curfile):
            # module docstring?
            if self.__freshmodule:
                if ttype == tokenize.STRING:
                    self.__addentry(safe_eval(tstring), lineno, isdocstring=1)
                    self.__freshmodule = 0
                elif ttype not in (tokenize.COMMENT, tokenize.NL):
                    self.__freshmodule = 0
                return
            # class docstring?
            if ttype == tokenize.NAME and tstring in ("class", "def"):
                self.__state = self.__suiteseen
                return
        if ttype == tokenize.NAME and tstring in opts.keywords:
            self.__state = self.__keywordseen

    def __suiteseen(self, ttype, tstring, lineno):  # skipcq: PYL-W0613
        # ignore anything until we see the colon
        if ttype == tokenize.OP and tstring == ":":
            self.__state = self.__suitedocstring

    def __suitedocstring(self, ttype, tstring, lineno):
        # ignore any intervening noise
        if ttype == tokenize.STRING:
            self.__addentry(safe_eval(tstring), lineno, isdocstring=1)
            self.__state = self.__waiting
        elif ttype not in (tokenize.NEWLINE, tokenize.INDENT, tokenize.COMMENT):
            # there was no class docstring
            self.__state = self.__waiting

    def __keywordseen(self, ttype, tstring, lineno):
        if ttype == tokenize.OP and tstring == "(":
            self.__data = []
            self.__lineno = lineno
            self.__state = self.__openseen
        else:
            self.__state = self.__waiting

    def __openseen(self, ttype, tstring, lineno):  # skipcq: PYL-W0613
        if ttype == tokenize.OP and tstring == ")":
            # We've seen the last of the translatable strings.  Record the
            # line number of the first line of the strings and update the list
            # of messages seen.  Reset state for the next batch.  If there
            # were no strings inside _(), then just ignore this entry.
            if self.__data:
                self.__addentry(EMPTYSTRING.join(self.__data))
            self.__state = self.__waiting
        elif ttype == tokenize.STRING:
            self.__data.append(safe_eval(tstring))
        elif ttype not in [
            tokenize.COMMENT,
            token.INDENT,
            token.DEDENT,
            token.NEWLINE,
            tokenize.NL,
        ]:
            # warn if we see anything else than STRING or whitespace
            print(
                _(
                    '*** {file}:{lineno}: Seen unexpected token "{token}"'.format(
                        token=tstring, file=self.__curfile, lineno=self.__lineno
                    )
                )
            )
            self.__state = self.__waiting

    def __addentry(self, msg, lineno=None, isdocstring=0):
        if lineno is None:
            lineno = self.__lineno
        if msg not in self.__options.toexclude:
            entry = (self.__curfile, lineno)
            self.__messages.setdefault(msg, {})[entry] = isdocstring

    def set_filename(self, filename):
        """Set the current filename."""
        self.__curfile = filename
        self.__freshmodule = 1

    def write(self, fp):
        """Write the gettext template."""
        options = self.__options
        timestamp = time.strftime("%Y-%m-%d %H:%M+%Z")
        # The time stamp in the header doesn't have the same format as that
        # generated by xgettext...
        print(pot_header % {"time": timestamp, "version": __version__}, file=fp)
        # Sort the entries.  First sort each particular entry's keys, then
        # sort all the entries by their first item.
        reverse = {}
        for k, v in list(self.__messages.items()):
            keys = sorted(v.keys())
            reverse.setdefault(tuple(keys), []).append((k, v))
        rkeys = sorted(reverse.keys())
        for rkey in rkeys:
            rentries = sorted(reverse[rkey])
            for k, v in rentries:
                isdocstring = 0
                # If the entry was gleaned out of a docstring, then add a
                # comment stating so.  This is to aid translators who may wish
                # to skip translating some unimportant docstrings.
                if reduce(operator.__add__, list(v.values())):
                    isdocstring = 1
                # k is the message string, v is a dictionary-set of (filename,
                # lineno) tuples.  We want to sort the entries in v first by
                # file name and then by line number.
                v = sorted(v.keys())
                if not options.writelocations:
                    pass
                # location comments are different b/w Solaris and GNU:
                elif options.locationstyle == options.SOLARIS:
                    for filename, lineno in v:
                        d = {"filename": filename, "lineno": lineno}
                        print(_("# File: %(filename)s, line: %(lineno)d") % d, file=fp)
                elif options.locationstyle == options.GNU:
                    # fit as many locations on one line, as long as the
                    # resulting line length doesn't exceeds 'options.width'
                    locline = "#:"
                    for filename, lineno in v:
                        d = {"filename": filename, "lineno": lineno}
                        s = _(" %(filename)s:%(lineno)d") % d
                        if len(locline) + len(s) <= options.width:
                            locline = locline + s
                        else:
                            print(locline, file=fp)
                            locline = "#:" + s
                    if len(locline) > 2:
                        print(locline, file=fp)
                if isdocstring:
                    print("#, docstring", file=fp)
                print("msgid", normalize(k), file=fp)
                print('msgstr ""\n', file=fp)


def main():
    """Run the script."""
    opts = ()
    args = []
    try:
        opts, args = getopt.getopt(
            sys.argv[1:],
            "ad:DEhk:Kno:p:S:Vvw:x:X:",
            [
                "extract-all",
                "default-domain=",
                "escape",
                "help",
                "keyword=",
                "no-default-keywords",
                "add-location",
                "no-location",
                "output=",
                "output-dir=",
                "style=",
                "verbose",
                "version",
                "width=",
                "exclude-file=",
                "docstrings",
                "no-docstrings",
            ],
        )
    except getopt.error as msg:
        usage(1, str(msg))

    # for holding option values
    class Options:
        # constants
        GNU = 1
        SOLARIS = 2
        # defaults
        extractall = 0  # FIXME: currently this option has no effect at all.
        escape = 0
        keywords: List[str] = []
        outpath = ""
        outfile = "messages.pot"
        writelocations = 1
        locationstyle = GNU
        verbose = 0
        width = 78
        excludefilename = ""
        toexclude: List[str] = []
        docstrings = 0
        nodocstrings = {}

    options = Options()
    locations = {"gnu": options.GNU, "solaris": options.SOLARIS}

    # parse options
    for opt, arg in opts:
        if opt in ("-h", "--help"):
            usage(0)
        elif opt in ("-a", "--extract-all"):
            options.extractall = 1
        elif opt in ("-d", "--default-domain"):
            options.outfile = arg + ".pot"
        elif opt in ("-E", "--escape"):
            options.escape = 1
        elif opt in ("-D", "--docstrings"):
            options.docstrings = 1
        elif opt in ("-k", "--keyword"):
            options.keywords.append(arg)
        elif opt in ("-K", "--no-default-keywords"):
            global default_keywords
            default_keywords = []
        elif opt in ("-n", "--add-location"):
            options.writelocations = 1
        elif opt in ("--no-location",):
            options.writelocations = 0
        elif opt in ("-S", "--style"):
            options.locationstyle = locations.get(arg.lower(), 1)
        elif opt in ("-o", "--output"):
            options.outfile = arg
        elif opt in ("-p", "--output-dir"):
            options.outpath = arg
        elif opt in ("-v", "--verbose"):
            options.verbose = 1
        elif opt in ("-V", "--version"):
            print(_("pygettext.py (xgettext for Python) {0}").format(__version__))
            sys.exit(0)
        elif opt in ("-w", "--width"):
            try:
                options.width = int(arg)
            except ValueError:
                usage(1, _("--width argument must be an integer: %s") % arg)
        elif opt in ("-x", "--exclude-file"):
            options.excludefilename = arg
        elif opt in ("-X", "--no-docstrings"):
            fp = open(arg)
            try:
                while True:
                    line = fp.readline()
                    if not line:
                        break
                    options.nodocstrings[line[:-1]] = 1
            finally:
                fp.close()

    # calculate escapes
    make_escapes(options.escape)

    # calculate all keywords
    options.keywords.extend(default_keywords)

    # initialize list of strings to exclude
    if options.excludefilename:
        try:
            with open(options.excludefilename) as fp:
                options.toexclude = fp.readlines()
        except IOError:
            print(
                _("Can't read --exclude-file: {0}").format(options.excludefilename),
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        options.toexclude = []

    # resolve args to module lists
    expanded = []
    for arg in args:
        if arg == "-":
            expanded.append(arg)
        else:
            expanded.extend(get_files_for_name(arg))
    args = expanded

    # slurp through all the files
    eater = TokenEater(options)
    for filename in args:
        if filename == "-":
            if options.verbose:
                print(_("Reading standard input"))
            fp = sys.stdin
            closep = 0
        else:
            if options.verbose:
                print(_("Working on {0}").format(filename))
            fp = open(filename, "rt")
            closep = 1
        try:
            eater.set_filename(filename)
            try:
                for tok in tokenize.generate_tokens(fp.readline):
                    eater(*tok)
            except tokenize.TokenError as e:
                print(f"{e}", file=sys.stderr)
        finally:
            if closep:
                fp.close()

    # write the output
    if options.outfile == "-":
        fp = sys.stdout
        closep = 0
    else:
        if options.outpath:
            options.outfile = os.path.join(options.outpath, options.outfile)
        fp = open(options.outfile, "w")
        closep = 1
    try:
        eater.write(fp)
    finally:
        if closep:
            fp.close()


if __name__ == "__main__":
    main()
    # some more test strings
    # _("a unicode string")
    # this one creates a warning
    # _('*** Seen unexpected token "%(token)s"') % {"token": "test"}
    # _("more" "than" "one" "string")
