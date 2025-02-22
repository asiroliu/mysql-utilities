#
# Copyright (c) 2011, 2016, Oracle and/or its affiliates. All rights reserved.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA
#

"""
This module contains classes and functions used to manage a console utility.
"""

import os
import sys
import shlex

from mysql_utilities.common.format import print_dictionary_list
from mysql_utilities.common.variables import Variables
from mysql_utilities.exception import UtilError

_COMMAND_COMPLETE = 0
_OPTION_COMPLETE = 1
_VARIABLE_COMPLETE = 2

# TODO remove this pylint disable regarding duplicate keys
# pylint: disable=W0109
_COMMAND_KEY = {
    '\x7f': 'DELETE_POSIX',
    '\x1b[3~': 'DELETE_MAC',
    '\x0a': 'ENTER_POSIX',
    '\r': 'ENTER_WIN',
    '\x1b': 'ESCAPE',
    '\x1b[A': 'ARROW_UP',
    '\x1b[B': 'ARROW_DN',
    '\x1b[C': 'ARROW_RT',
    '\x1b[D': 'ARROW_LT',
    '\t': 'TAB',
    '\x7f': 'BACKSPACE_POSIX',
    '\xe0': 'SPECIAL_WIN',
    '\x08': 'BACKSPACE_WIN',
    '\x1bOH': 'HOME',
    '\x1bOF': 'END'
}

# Some windows keys are different and require reading two keys.
# The following are the second characters.
_WIN_COMMAND_KEY = {
    'S': 'DELETE_WIN',
    'H': 'ARROW_UP',
    'P': 'ARROW_DN',
    'M': 'ARROW_RT',
    'K': 'ARROW_LT',
    'G': 'HOME',
    'O': 'END'
}

_COMMAND_COMPLETE = 0
_OPTION_COMPLETE = 1
_VARIABLE_COMPLETE = 2

# Base commands for all consoles.
#
# The list includes a tuple for each command that contains the name of the
# command, an alias (if defined) and its help text.
_BASE_COMMANDS = [
    {'name': 'help',
     'alias': 'help commands',
     'text': 'Show this list.'},
    {'name': 'exit',
     'alias': 'quit',
     'text': 'Exit the console.'},
    {'name': 'set <variable>=<value>',
     'alias': '',
     'text': 'Store a variable for recall in commands.'},
    {'name': 'show options',
     'alias': '',
     'text': 'Display list of options specified by the user on launch.'},
    {'name': 'show variables',
     'alias': '',
     'text': 'Display list of variables.'},
    {'name': '<ENTER>',
     'alias': '',
     'text': 'Press ENTER to execute command.'},
    {'name': '<ESCAPE>',
     'alias': '',
     'text': 'Press ESCAPE to clear the command entry.'},
    {'name': '<DOWN>',
     'alias': '',
     'text': 'Press DOWN to retrieve the previous command.'},
    {'name': '<UP>',
     'alias': '',
     'text': 'Press UP to retrieve the next command in history.'},
    {'name': '<TAB>',
     'alias': '',
     'text': 'Press TAB for type completion of utility, '
             'option, or variable names.'},
    {'name': '<TAB><TAB>',
     'alias': '',
     'text': 'Press TAB twice for list of matching type '
             'completion (context sensitive).'}
]


# Try to import the windows getch() if it fails, we're on Posix so define
# a custom getch() method to return keys.
try:
    # Win32
    # pylint: disable=C0413
    from msvcrt import getch  # pylint: disable=F0401
except ImportError:
    # UNIX/Posix
    # pylint: disable=C0411,C0413
    import termios

    def getch():
        """getch function
        """
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        new = termios.tcgetattr(fd)
        new[3] = new[3] & ~termios.ICANON & ~termios.ECHO
        new[6][termios.VMIN] = 1
        new[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, new)
        key = None
        try:
            key = os.read(fd, 80)
        finally:
            termios.tcsetattr(fd, termios.TCSAFLUSH, old)
        return key


class _CommandHistory(object):
    """
    The _CommandHistory class encapsulates a list of commands that can be
    retrieved either via the previous or next command in the list. The
    list grows to a circular list of max size as specified at initialization.
    """

    def __init__(self, options=None):
        """Constructor

        options[in]        Options for the class member variables
        """
        if options is None:
            options = {}
        self.position = 0
        self.commands = []
        self.max_size = options.get('max_size', 40)

    def add(self, command):
        """Add a command to the history list

        This method appends the command list if the max size is not met or
        replaces the last entry if the max size has been met.
        """
        if len(self.commands) < self.max_size:
            self.commands.append(command)
            self.position = 0
        else:
            if self.position == 0:
                self.commands[self.max_size - 1] = command
            else:
                self.commands[self.position - 1] = command

    def __next__(self):
        """Get next command in list.

        Returns string next command
        """
        if len(self.commands) == 0:
            return ''
        if self.position == len(self.commands) - 1:
            self.position = 0
        else:
            self.position += 1
        return self.commands[self.position]

    def previous(self):
        """Get previous command in list.

        Returns string prev command
        """
        if len(self.commands) == 0:
            return ''
        if self.position == 0:
            self.position = len(self.commands) - 1
        else:
            self.position -= 1
        return self.commands[self.position]


class _Command(object):
    """
    The _Command class encapsulates the operations of a console command line.
    """

    def __init__(self, prompt):
        """Constructor

        prompt[in]         The prompt written to the screen after each command
        """
        self.prompt = prompt
        self.position = 0
        self.command = ""
        self.length = 0

    @staticmethod
    def _erase_portion(num):
        """Erase a portion of the command line using backspace and spaces.

        num[in]            Number of spaces to erase starting from cursor left
        """
        i = 0
        while i < num:
            sys.stdout.write('\b')
            sys.stdout.write(' ')
            sys.stdout.write('\b')
            i += 1

    def get_command(self):
        """Return the current command.

        Returns string - the current command
        """
        return self.command

    def get_nearest_option(self):
        """Get the option for tab completion that is closest to the cursor

        This method returns the portion of the command line nearest the cursor
        and to the left until a space is encountered. For example, if the
        cursor was one space to the right of 'b' in some_command --verb --some
        it would return '--verb' or if the cursor is at the end of the command
        it will return the last portion of the command. In the previous
        example it would return '--some'.

        This portion is used for tab completion of options.

        Returns string - most local portion of the command.
        """
        parts = self.command.split(' ')
        # if not at the end of the command line, return the phrase where
        # the cursor is located indicated by self.position
        if self.position < self.length:
            for i in range(self.position - 1, len(parts[0]) - 1, -1):
                if self.command[i] == ' ':
                    return self.command[i + 1:self.position].strip(' ')
            return 'ERROR'
        else:
            return parts[len(parts) - 1]

    def erase_command(self):
        """Erase the command and reprint the prompt.
        """
        sys.stdout.write(' ' * (self.length - self.position))
        self._erase_portion(self.length)
        self.command = ''

    def _erase_inline(self, backspace=True):
        """Adjust command line by removing current char

        backspace[in]      If True, erase to the left (backspace)
                           If False, erase to the right
        """
        if self.position < self.length:
            num_erase = 1 + self.length - self.position
            if backspace:
                sys.stdout.write('\b')
            i = 0
            while i < num_erase:
                sys.stdout.write(' ')
                i += 1
            i = 0
            while i < num_erase:
                sys.stdout.write('\b')
                i += 1
        elif backspace:
            self._erase_portion(1)

    def home_keypress(self):
        """Executes the 'HOME' key press.

        This moves the cursor to the beginning of the command.
        """
        tmp = self.position
        self.position = 0
        sys.stdout.write('\b' * tmp)

    def end_keypress(self):
        """Executes the 'END' key press.

        This moves the cursor to the end of the command.
        """
        sys.stdout.write(self.command[self.position:self.length])
        self.position = self.length

    def delete_keypress(self):
        """Execute the 'DELETE' key press.

        This deletes one character from the right of the cursor.
        """
        if self.position < self.length:
            if self.length == 1:
                self.command = ''
                sys.stdout.write(' ')
                sys.stdout.write('\b')
                self.length = 0
            elif self.length > 0:
                self._erase_inline(False)
                old_command = self.command
                self.command = old_command[0:self.position]
                if self.position < self.length:
                    self.command += old_command[self.position + 1:]
                sys.stdout.write(self.command[self.position:])
                self.length = len(self.command)
                spaces = len(self.command[self.position:])
                # pylint: disable=W0612
                for i in range(0, spaces):
                    sys.stdout.write('\b')
                sys.stdout.flush()

    def backspace_keypress(self):
        """Execute the 'BACKSPACE' key press.

        This deletes one character to the left of the cursor.
        """
        # Here we need to move back one character calculating for in-string
        # edits (self.position < self.length)
        # if position less than length, we're inserting values
        if self.position <= 0:
            return
        if self.position < self.length:
            self._erase_inline(True)
            # build new command
            self.command = self.command[0:self.position - 1] + \
                self.command[self.position:]
            sys.stdout.write(self.command[self.position - 1:])
            i = 0
            while i < (self.length - self.position):
                sys.stdout.write('\b')
                i += 1
        else:
            self._erase_portion(1)
            self.command = self.command[0:self.length - 1]
        self.length -= 1
        self.position -= 1

    def left_arrow_keypress(self):
        """Execute the 'LEFT ARROW' keypress

        This moves the cursor position one place to the left until the
        beginning of the command.
        """
        if self.position > 0:
            self.position -= 1
            sys.stdout.write('\b')

    def right_arrow_keypress(self):
        """Execute the 'RIGHT ARROW' keypress

        This moves the cursor to the right one space until the end of the
        command.
        """
        # Here we need to move to the right one space but we don't have a
        # forward space print character. So we reprint the one character where
        # the position indicator is.
        if self.position < self.length:
            sys.stdout.write(self.command[self.position:self.position + 1])
            self.position += 1

    def replace_command(self, new_cmd):
        """Replace the command with a new command.

        This replaces the command and redisplays the prompt and new command.
        """
        if new_cmd != '':
            self._erase_portion(self.length)
            self.command = new_cmd
            sys.stdout.write(self.command)
            self.position = len(self.command)
            self.length = len(self.command)

    def add(self, key):
        """Add one or more characters to the command

        This method adds the characters specified in key to the command based
        on the location of the cursor. If in-string, the characters will be
        inserted accordingly or if at the end of the command (if cursor at
        the end).
        """
        if key is None:
            return
        # if position less than length, we're inserting values
        if self.position < self.length:
            # erase position forward.
            num_erase = self.length - self.position
            i = 0
            while i < num_erase:
                sys.stdout.write(' ')
                i += 1
            i = 0
            while i < num_erase:
                sys.stdout.write('\b')
                i += 1
            # build new command
            self.command = self.command[0:self.position] + key + \
                self.command[self.position:]
            sys.stdout.write(self.command[self.position:])
            self.position += len(key)
            # move cursor back to location at end of new key
            i = 0
            while i < (self.length - self.position + len(key)):
                sys.stdout.write('\b')
                i += 1
            self.length += len(key)
        else:
            self.command += key
            sys.stdout.write(key)
            self.position += len(key)
            self.length += len(key)

    def display_command(self):
        """Redisplay the command
        """
        sys.stdout.write(self.prompt + self.command)
        sys.stdout.flush()

    def clear(self):
        """Clear the command line - user must get the command first.
        """
        self.command = ''
        self.position = 0
        self.length = 0


class Console(object):
    """Console class
    """
    def __init__(self, new_base_commands, options):
        """Constructor

        new_base_commands  Additions to the base commands
        options[in]        Options for the class member variables
        """
        self.options = options
        self.tab_count = 0
        self.base_commands = []
        self.base_commands.extend(new_base_commands)
        self.base_commands.extend(_BASE_COMMANDS)
        self.type_complete_mode = _COMMAND_COMPLETE
        self.cmd_line = _Command(self.options.get('prompt', '> '))
        self.width = self.options.get('width', 80)
        self.commands = self.options.get("commands", None)
        self.custom_commands = self.options.get("custom", False)
        self.quiet = self.options.get("quiet", False)
        self.variables = Variables(options)
        self.history = _CommandHistory({'max_size': 20})
        self.position = 0
        self.errors = []
        var_list = self.options.get('variables', [])
        for var in var_list:
            self.variables.add_variable(var['name'], var['value'])

    def show_errors(self):
        """Show errors

        Displays the errors captured when executing an utility.
        """
        if self.quiet:
            return
        if not self.errors:
            print()
            print("No errors to display.\n")
        for error in self.errors:
            print()
            print(("{0}\n".format(error)))

    def clear_errors(self):
        """Clear errors

        Clears captured errors occurring while executing an utility.
        """
        if self.quiet:
            return
        self.errors = []
        print()

    def show_last_error(self):
        """Show errors

        Displays the last error occurred when executing an utility.
        """
        if self.quiet:
            return
        if not self.errors:
            print()
            print("No error to display.\n")
        else:
            print()
            print(("{0}\n".format(self.errors[-1])))

    def show_custom_command_help(self, arg):
        """Display the help for a custom command.

        Note: Override this method for help on custom commands.

        arg[in]            Help command argument
        """
        if self.quiet:
            return
        print("\nNo commands like '%s' exist.\n" % arg)

    def do_custom_tab(self, prefix):
        """Do custom tab key processing

        Note: Override this method for tab completion for custom commands.

        prefix[in]        Prefix of the custom command
        """
        pass

    def do_custom_option_tab(self, prefix):
        """Do custom command option tab key processing

        Note: Override this method for tab completion for options for custom
        commands.

        prefix[in]        Prefix of the custom command
        """
        if self.quiet:
            return
        print("\n\nNo custom commands found.\n")

    @staticmethod
    def is_valid_custom_command(command_text):
        """Is command a valid custom command?

        This method evaluates the custom command for validity.

        Note: Override this command to determine if command_text is a valid
        custom command.

        command_text[in]   The complete command as entered by user

        Returns bool - True if valid, False if not recognized
        """
        return False  # return False by default if method not overridden

    def execute_custom_command(self, command, parameters):
        """Execute a custom command.
        Note: Override this method to execute a custom command.
        """
        pass

    def show_custom_options(self):
        """Show custom options

        Note: Override this for 'show options' functionality.
        """
        if self.quiet:
            return

    def do_option_tab(self, prefix):
        """Do tab completion for options

        This method will search for an option using the prefix passed. It
        first searches the console commands defined at instantiation (the
        general commands for the shell) and if not found, checks the
        options for a custom command.

        prefix[in]        Prefix of the option
        """
        full_command = self.cmd_line.get_command()
        matches = self.get_commands(full_command.strip(' '))
        if len(matches) > 0:
            self.do_base_command_tab(full_command, matches)
        elif self.custom_commands:
            # if prefix is 'help', try command complete for custom commands
            if full_command[0:4].lower() == 'help':
                self.do_custom_tab(prefix)
            else:
                self.do_custom_option_tab(prefix)

    def _set_complete_mode(self):
        """Set the tab completion mode

        If the command buffer is only 1 part (command and no options),
        we are in _COMMAND_COMPLETE mode.

        Else if the nearest option contains a $ at the start, we are in
        _VARIABLE_COMPLETE mode.

        Else we are in _OPTION_COMPLETE mode.

        _COMMAND_COMPLETE = tab complete for base and custom commands
        _VARIABLE_COMPLETE = tab complete for user-defined variables
        _OPTION_COMPLETE = tab complete for base or custom command options
        """
        buf = self.cmd_line.get_command()
        parts = buf.split(' ')
        segment = ''
        if (len(buf) > 0 and len(parts) == 1):
            self.type_complete_mode = _COMMAND_COMPLETE
        else:
            segment = self.cmd_line.get_nearest_option()
            if segment.find('$') > 0:
                self.type_complete_mode = _VARIABLE_COMPLETE
            else:
                self.type_complete_mode = _OPTION_COMPLETE
        return segment

    def show_help(self, parameter):
        """Display the help for either all commands or the help for a
        custom command.

        parameter[in]      Any parameter for the help command.
                           For example, 'help commands'
        """
        if self.quiet:
            return
        if not parameter or (parameter and parameter.lower() == 'commands'):
            print()
            print_dictionary_list(['Command', 'Description'],
                                  ['name', 'text', 'alias'],
                                  self.base_commands, self.width, True)
            print()
        else:
            matches = self.get_commands(parameter)
            if len(matches) > 0:
                self.show_command_help(matches)
            elif self.custom_commands:
                self.show_custom_command_help(parameter)

    def do_variable_tab(self, segment):
        """Do the tab completion for a variable

        This method will attempt to find a variable in the list of user-
        defined variables and complete the name of variable. If the user
        types 'TAB' twice, it will display a list of all possible matches.
        """
        # find the last $
        variable = ''
        start_var = 0
        new_var = ''
        stop = len(segment)
        for i in range(stop - 1, 0, -1):
            if segment[i] == ' ':
                break
            elif segment[i] == '$':
                variable = segment[i + 1:]
                start_var = i

        if start_var == stop:
            # show all of the variables
            matches = self.variables.get_matches({})
        else:
            matches = self.variables.get_matches(variable)

        if self.tab_count == 2:
            if len(matches) > 0:
                self.variables.show_variables(matches)
            else:
                self.variables.show_variables({})
            self.cmd_line.display_command()
            self.tab_count = 0
        else:
            # Do command completion here
            if len(matches) == 1:
                new_var = list(matches[0].items())[0][0] + ' '
                self.cmd_line.add(new_var[len(variable):])
                self.tab_count = 0

    def do_command_tab(self, command_text):
        """Do the tab completion for a command

        If the command is in the base commands, complete it there. If not,
        attempt to perform tab completion for custom commands (if defined).
        """
        # See if command is in the base command list first
        matches = self.get_commands(command_text)
        if len(matches) > 0:
            self.do_base_command_tab(command_text, matches)
        # Ok, not in command list, now check custom commands
        elif self.custom_commands:
            self.do_custom_tab(command_text)

    def do_base_command_tab(self, command_text, matches):
        """Do the tab completion for a base command.

        This method prints the list of base commands that match the
        command. If the user pressed TAB twice, it displays the list of all
        matches. If a single match is found, it returns the balance of the
        command.

        Note: this method gets its matches from do_command_tab.

        command_text[in]   Command
        matches[in]        Known matches (from do_command_tab)
        """
        if self.tab_count == 2:
            print("\n")
            print_dictionary_list(['Command', 'Description'],
                                  ['name', 'text', 'alias'],
                                  matches, self.width, True)
            print()
            self.cmd_line.display_command()
            self.tab_count = 0
        else:
            if len(matches) == 1:
                if matches[0]['name'][:len(command_text)] == command_text:
                    new_cmd = matches[0]['name'] + ' '
                else:
                    new_cmd = matches[0]['alias'] + ' '
                self.tab_count = 0
                self.cmd_line.add(new_cmd[len(command_text):])

    def get_commands(self, cmd_prefix):
        """Get list of commands that match a prefix

        cmd_prefix[in]  prefix for name of command

        Returns dictionary entry for command based on matching first n chars
        """
        matches = []
        stop = len(cmd_prefix)
        start = 0
        for cmd in self.base_commands:
            if cmd['name'][start:stop] == cmd_prefix or \
               cmd['alias'][start:stop] == cmd_prefix:
                matches.append(cmd)

        return matches

    def show_command_help(self, commands):
        """Show the help for a list of commands.

        commands[in]       List of commands
        """
        if self.quiet:
            return
        print()
        print_dictionary_list(['Command', 'Description'],
                              ['name', 'text', 'alias'],
                              commands, self.width, True)
        print()

    def _do_command(self, command):
        """Execute a command

        This method routes the command to the appropriate methods for
        execution.

        command[in]        Command to execute

        Returns bool True - exit utility, False - do not exit
        """
        # do variable replacement
        command = self._replace_variables(command.strip(' '))
        if self.options.get('verbosity', False):
            print("\nExecuting command:", command)
        # process simple commands
        if command.lower().startswith('set '):
            self._add_variable(command[4:])
            if not self.quiet:
                print()
        elif command[0:11].lower() == 'show errors':
            self.show_errors()
        elif command[0:12].lower() == 'clear errors':
            self.clear_errors()
        elif command[0:15].lower() == 'show last error':
            self.show_last_error()
        elif command[0:14].lower() == 'show variables':
            self.variables.show_variables()
        elif self.custom_commands and command[0:12].lower() == 'show options':
            self.show_custom_options()
        else:
            cmd, parameters = self._get_util_parameters(command)
            if cmd is None:
                return False
            else:
                if cmd.lower() == 'help':
                    token = parameters[0] if parameters else ''
                    self.show_help(token)
                    self.cmd_line.clear()
                    self.tab_count = 0
                elif cmd == '':
                    print()
                elif cmd.lower() in ['exit', 'quit']:
                    print()
                    return True
                elif self.custom_commands:
                    if not self.is_valid_custom_command(cmd):
                        print(("\n\nUnknown command: {0} {1}\n"
                              "".format(cmd, ' '.join(parameters))))
                    else:
                        try:
                            self.execute_custom_command(cmd, parameters)
                            print()
                        except UtilError as err:
                            print(err.errmsg)

        self.cmd_line.clear()
        self.tab_count = 0
        return False

    def _process_command_keys(self, cmd_key):
        """Do the action associated with a command key.

        This method will act on the recognized command keys and execute the
        effect for each.

        cmd_key[in]        Key pressed
        """
        if cmd_key in ['ESCAPE']:
            self.cmd_line.erase_command()
        elif cmd_key in ['DELETE_POSIX', 'DELETE_WIN', 'DELETE_MAC']:
            self.cmd_line.delete_keypress()
            self.tab_count = 0
        elif cmd_key == 'ARROW_UP':
            self.cmd_line.replace_command(self.history.previous())
        elif cmd_key == 'ARROW_DN':
            self.cmd_line.replace_command(next(self.history))
        elif cmd_key == 'ARROW_LT':
            self.cmd_line.left_arrow_keypress()
        elif cmd_key == 'ARROW_RT':
            self.cmd_line.right_arrow_keypress()
        elif cmd_key in ['BACKSPACE_POSIX', 'BACKSPACE_WIN']:
            self.cmd_line.backspace_keypress()
        elif cmd_key == 'HOME':
            self.cmd_line.home_keypress()
        elif cmd_key == 'END':
            self.cmd_line.end_keypress()
        else:  # 'TAB'
            segment = self._set_complete_mode()
            self.tab_count += 1
            if self.type_complete_mode == _COMMAND_COMPLETE:
                self.do_command_tab(self.cmd_line.get_command())
            elif self.type_complete_mode == _OPTION_COMPLETE:
                self.do_option_tab(segment)
            else:  # _VARIABLE_COMPLETE
                self.do_variable_tab(segment)
        cmd_key = ''

    def _add_variable(self, set_command):
        """Add a variable to the list of variables.

        This method adds the user-defined variable to the internal list.

        set_command[in]    Set command from the user
        """
        if set_command.find('=') <= 0:
            print("\n\nSET command invalid. Syntax: SET <NAME> = <value>")
            return

        # get name and value
        name, value = set_command.split('=')
        name = name.strip().strip('$')
        value = value.strip()
        self.variables.add_variable(name, value)

    def _replace_variables(self, cmd_string):
        """Replace user-defined variables with values from the internal list.

        This method replaces $VARNAME with the value stored when the set
        command was issued.

        cmd_string[in]     Command from the user

        Returns string - command string with replacements
        """
        i = 1
        new_cmd = cmd_string
        while i > 0:
            i = new_cmd.find('$', i)
            if i > 0:
                j = new_cmd.find(' ', i)
                if j == -1:
                    j = len(new_cmd)
                if j > i:
                    var_name = new_cmd[i + 1:j]
                    var = self.variables.find_variable(var_name)
                    if var is not None:
                        new_cmd = new_cmd[0:i] + var[var_name] + new_cmd[j:]
                    else:
                        i = j

        return new_cmd

    @staticmethod
    def _get_util_parameters(cmd_string):
        """Split the command name from the command and return balance as
        parameters.

        cmd_string[in]     Command

        Returns tuple - command, list of parameters
        """
        try:
            tokens = shlex.split(cmd_string)
        except ValueError as err:
            print()
            print(("WARNING: Unable to execute command, reason: {0}"
                  "".format(str(err))))
            return None, None
        else:
            if len(tokens) > 1:
                return tokens[0], tokens[1:]
        return cmd_string.strip(' '), []

    def get_user_command(self):
        """Get a command from the user.

        This method displays a prompt to the user and returns when one of
        the command keys is pressed.
        """
        self.cmd_line.display_command()
        cmd_string = ''
        cmd_key = None
        self.tab_count = 0
        while cmd_key not in ['ENTER_POSIX', 'ENTER_WIN']:
            key = getch()
            # If a special key, act on it
            if key in _COMMAND_KEY:
                cmd_key = _COMMAND_KEY[key]
                # Windows does things oddly for some keys
                if os.name != 'posix' and cmd_key == 'SPECIAL_WIN':
                    key = getch()
                    cmd_key = _WIN_COMMAND_KEY.get(key)
                    if cmd_key is None:
                        continue
                self._process_command_keys(cmd_key)
                cmd_string = self.cmd_line.get_command()
            # else add key to command buffer
            else:
                cmd_string = self.cmd_line.get_command()
                self.cmd_line.add(key)
                cmd_string = self.cmd_line.get_command()
            sys.stdout.flush()

        self.position = 0
        return cmd_string

    def run_console(self, lines=None):
        """Run the console.

        This method is the main loop for executing commands. For all subclassed
        classes, the user need only call this method to execute an interactive
        shell or execute commands and exit. It can be used in three modes:

        1) it can process commands passed via lines list
        2) it can process commands passed via a pipe to the python exec
        3) it can prompt for commands and execute them as entered

        Modes (1) and (2) execute all commands then exit.

        lines[in]          If not empty, execute the list of commands.
        """
        if not lines:
            lines = []
        # If we have commands issued by the command line, execute and exit.
        if self.commands is not None:
            command_list = self.commands.split(';')
            for command in command_list:
                command = command.strip('\n').strip(' ')
                if os.name == 'nt':
                    command = command.strip('"')
                if self._do_command(command.strip('"')):
                    break

        # If we have piped input, read the input by line and execute
        elif not os.isatty(sys.stdin.fileno()) or len(lines) > 0:
            for command in sys.stdin.readlines():
                command_list = command.split(';')
                for cmd in command_list:
                    cmd = cmd.strip('\n').strip(' ')
                    if os.name == 'nt':
                        cmd = cmd.strip('"')
                    if self._do_command(cmd.strip('"')):
                        break

        # Otherwise, we are in an interactive mode where we get a command
        # from the user and execute
        else:
            cmd = ''
            if not self.quiet:
                print(self.options.get('welcome', 'Welcome to the console!\n'))
            while cmd.lower() not in ['exit', 'quit']:
                command = self.get_user_command()
                self.history.add(command)
                if self._do_command(command):
                    break
            if not self.quiet:
                print(self.options.get('goodbye',
                                       'Thanks for using the console.\n'))
