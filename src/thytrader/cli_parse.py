"""Argparse helpers so global flags may precede or follow a subcommand.

``ArgumentParser.parse_intermixed_args`` cannot be used with subparsers.
"""

from __future__ import annotations

import argparse


def trailing_options(shared: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Copy optional flags from ``shared`` with suppressed defaults.

    Subparsers otherwise reset parent ``store_true`` and defaulted options to
    their own defaults, dropping flags that appeared before the command.
    """
    trailing = argparse.ArgumentParser(add_help=False)
    for action in shared._actions:
        if not action.option_strings or action.dest == "help":
            continue
        if action.nargs == 0 and action.const is True:
            trailing.add_argument(
                *action.option_strings,
                action="store_true",
                default=argparse.SUPPRESS,
                help=action.help,
            )
            continue
        trailing.add_argument(
            *action.option_strings,
            default=argparse.SUPPRESS,
            choices=action.choices,
            help=action.help,
        )
    return trailing
