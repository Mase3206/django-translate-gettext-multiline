from __future__ import annotations

import re
from pathlib import Path

from deep_translator import GoogleTranslator
from deep_translator.exceptions import LanguageNotSupportedException
from django.conf import settings

from django_translate_gettext.exceptions import TranslatorError


class PoFileTranslator:
    def __init__(self, lang_code: str):
        self.lang_code = lang_code
        self.locale_paths = [Path(filepath) for filepath in settings.LOCALE_PATHS]
        try:
            self.translator = GoogleTranslator(source="auto", target=lang_code)
        except LanguageNotSupportedException as error:
            raise TranslatorError(f"Language code {lang_code} is not supported by the translator") from error

    def translate_block(self, block: str, msgid: list[str]) -> str:
        msgstr = re.findall(r'msgstr "(.*?)"', block)
        if msgstr and msgstr[0]:
            return block
        translated = self.translator.translate(msgid[0])
        return re.sub(r'msgstr "(.*?)*"', f'msgstr "{translated}"', block)

    def process_first_block(self, block: str) -> str:
        parts = block.split("\n")
        raw_msgid, raw_msgstr = parts[-2], parts[-1]
        msgid = re.findall(r'msgid "(.*?)"', raw_msgid)
        parts[-1] = self.translate_block(block=raw_msgstr, msgid=msgid)
        return "\n".join(parts)
    
    def translate_multi_line_block(self, multiline_block: MultilineText) -> str:
        msgid = multiline_block.content
        translated = self.translator.translate(msgid)[:-2]  # adds an escaped \\ at the very end for some reason, so let's get rid of it
        indent = ''.join([' ' for i in range(multiline_block.indentation_depth)])
        
        lines = []
        chunks = len(translated)
        chunk_size = 80

        i = 0
        j = chunk_size + i
        while i < chunks:
            j = min(chunk_size + i, chunks)
            # first line should be indented
            if i == 0:
                lines.append(f'"{indent}{translated[i:j - multiline_block.indentation_depth]}"')
                i += chunk_size - multiline_block.indentation_depth
            # we've run out of text
            elif translated[i:j] == '':
                i += chunk_size
                continue
            # remove the extra \\n from the line and append it
            elif translated[i:j][-2:] == '\\n':
                lines.append(f'"{translated[i:j][:-2]}"')
                i += chunk_size
            # append the line without modification
            else:
                lines.append(f'"{translated[i:j]}"')
                i += chunk_size

        lines[-1] = lines[-1][:-1] + '\\\\n' + '"'

        replace = [
            '""',
            '"\\\\n"',
            f'"{indent}"',
            *lines,
            f'"{indent}"'
        ]
        # print(replace)
        return re.sub(r'msgstr "(.*?)"', f'msgstr {'\n'.join(replace)}', multiline_block.block)

    def translate_locale_path(self, *, locale_path: Path) -> None:
        result = []
        po_file = locale_path.joinpath(self.lang_code, "LC_MESSAGES", "django.po")
        if not po_file.exists():
            raise TranslatorError(f"The file for code {self.lang_code} does not exist.")

        file_content = po_file.read_text().split("\n\n")
        for block in file_content:
            if not block:
                continue

            msgid = re.findall(r'msgid "(.*?)"', block)
            if len(msgid) != 1:
                result.append(self.process_first_block(block=block))
                continue
            elif msgid[0] == '':
                multiline_text = MultilineText.try_create_multline(block)
                if multiline_text:
                    multiline_text.get_content()
                    result.append(self.translate_multi_line_block(multiline_text))
                    continue

            result.append(self.translate_block(block=block, msgid=msgid))

        po_file.write_text("\n\n".join(result))

    def translate_codes(self) -> None:
        for locale_path in self.locale_paths:
            self.translate_locale_path(locale_path=locale_path)


class MultilineText:
    def __init__(self, block: str, regex_block: tuple = ()):
        self.indentation_depth: int = None #type:ignore

        if regex_block != '':
            self.block = block
            self.regex_block = regex_block
        else:
            self.block = block
            self.regex_block = MultilineText.get_regex_block(block)

        
    def get_indentation_depth(self, string: str) -> int:
        """
        Quick and dirty way to (maybe) find the indentation depth of the string.
        """
        example = '    "'
        reduced = ''
        reduced = string[1:] if string[0] == '"' or string[0] == "'" else string
        reduced = string[:-2] if string[-1] == '"' or string[-1] == "'" else string
        return (len(reduced) + 1)
    
    def get_content(self):
        """
        Extract the text from the msgid, set it to the self.content variable, and return it.
        """
        mightBeContent: list[str] = []
        check_strings = ['msgid', '', 'msgstr', '\\n']
        check_punctuation = ['', '"', "'", '""', "''"]

        for line in self.regex_block:
            ls = line.split()
            if not any(map(lambda v: v in ls, check_strings)) and ls != []:
            # if line.split() not in ['msgid', '', 'msgstr']:
                # strip out the quotation marks and whitespace
                stripped = line[1:-1].strip()

                # remove embedded newlines, excess whitespace, and extraneous quotation marks
                cleaned = []
                for s in stripped.split('"\n"'):
                    if s.strip() in check_punctuation:
                        if not self.indentation_depth:
                            self.indentation_depth = self.get_indentation_depth(s) 
                    else:
                        cleaned.append(s)
                    # cleaned = ''.join([s for s in stripped.split('"\n"') if s.strip() not in check_punctuation])
                cleaned = ''.join(cleaned)
                
                # remove escaped newline, if it exists
                if cleaned[-3:] == '\\n':
                    cleaned = cleaned[-3:]
                
                # append to the content
                if cleaned != '':
                    mightBeContent.append(cleaned)

        if not self.indentation_depth:
            self.indentation_depth = 0
        
        self.content = ' '.join(mightBeContent)
        return self.content

    @staticmethod
    def get_regex_block(block: str) -> tuple[str]:
        """
        Get the first element in regex `Match` object matching multi-line msgid elements as formatted by this module.
        """
        try:
            return re.findall(r'(msgid \"(.*?)\"\n\"\\n\"\n)((\".*\"\n)+)(msgstr)', block)[0]
        except IndexError:
            return () #type:ignore


    @staticmethod
    def try_create_multline(block: str) -> MultilineText | None:
        regex = MultilineText.get_regex_block(block)
        if len(regex) > 0:
            return MultilineText(block, regex_block=regex)
