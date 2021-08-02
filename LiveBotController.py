import enum
import os
import random
import re
import time
from typing import Optional

import yaml
from mcdreforged.api.all import *

PLUGIN_METADATA = {
    'id': 'livebot_controller',
    'version': '0.2.1',
    'name': 'LiveBotController',
    'description': "A MCDR plugin for controlling livebot",
    'author': ['Youmiel','YehowahLiu'],
    'link': 'https://github.com/FAS-Server/LiveBotController',
    'dependencies': {
        'mcdreforged': '>=1.0.0',
    }
}

STATE_PATTERN = re.compile(r'Bot state: (Normal|Offline|Spectating [\w]{3,16})')
# Botstate: Offline | Normal | Spectating <player>
LIST_PATTERN = re.compile(r'There are ([0-9]+) of a max of ([0-9]+) players online:\s?(.*)')
# group(1): player number
# group(2): max players
# group(3): player list
CONFIG_PATH = os.path.join('config', 'LiveBotController.yml')
LIVEBOT_CONFIG = os.path.join('server', 'LiveBotFabric', 'config.json')
LANDSCAPE_PATH = os.path.join('config', 'LiveBotLandscape.txt')

PREFIX = "!!live"

default_config = {
    'randomTpDelay': 30,
    'excludedPrefix': '',
    'excludedSuffix': '',
}
config = default_config.copy()


# -------------------------------------------
class PlayerStack:
    players: list
    size: int

    def __init__(self) -> None:
        self.players = []
        self.size = 0

    def push(self, player: str):
        if player in self.players:
            self.players.remove(player)
            self.size -= 1
        self.players.append(player)
        self.size += 1

    def pop(self) -> Optional[str]:
        if self.size > 0:
            player = self.players[self.size - 1]
            self.players.remove(player)
            self.size -= 1
            return player
        else:
            return None

    def top(self):
        if self.size > 0:
            return self.players[self.size - 1]
        else:
            return None


class LiveBotController:
    class Mode(enum.Enum):
        EMPTY = 'EMPTY'
        OCCUPIED = 'OCCUPIED'
        RANDOM = 'RANDOM'

    def __init__(self) -> None:
        self.online = False
        self.running = False
        self.mode = LiveBotController.Mode.EMPTY
        self.occupied_players = PlayerStack()
        self.time_since_last_tp = time.time()

    def start(self) -> None:
        self.running = True
        cast('bot_start')
        self.tick()

    @new_thread('LiveBotController')
    def tick(self):
        while self.running:
            if self.online:
                if self.occupied_players.size == 0 and self.mode != LiveBotController.Mode.RANDOM:
                    self.mode = LiveBotController.Mode.RANDOM
                if self.occupied_players.size > 0 and self.mode != LiveBotController.Mode.OCCUPIED:
                    self.mode = LiveBotController.Mode.OCCUPIED
                {
                    LiveBotController.Mode.EMPTY: self.do_empty,
                    LiveBotController.Mode.OCCUPIED: self.do_occupied,
                    LiveBotController.Mode.RANDOM: self.do_random,
                }[self.mode]()
            time.sleep(1)
        cast('bot_stop')

    def do_empty(self):  # really empty :)
        pass

    def do_occupied(self):
        global plugin_fields
        if self.occupied_players.top() not in plugin_fields.player_list:
            self.occupied_players.pop()
            if self.occupied_players.size != 0:
                plugin_fields.server.rcon_query("botfollow %s" % self.occupied_players.top())

    def do_random(self):
        global plugin_fields, config
        if (time.time() - self.time_since_last_tp) < config['randomTpDelay']:
            return
        self.time_since_last_tp = time.time()
        if self.online and plugin_fields.player_num <= 1:
            if plugin_fields.landscape_num > 0:
                index = random.randint(0, plugin_fields.landscape_num - 1)
                plugin_fields.server.rcon_query(plugin_fields.landscapes[index])
        elif self.online:
            '''
            pattern = plugin_fields.player_pattern
            while(plugin_fields.player_num > 1):
                index = random.randint(0, plugin_fields.player_num - 1)
                player = plugin_fields.player_list[index]
                if re.fullmatch(pattern, player) is None: 
                    break
                # old logic
            '''
            index = random.randint(0, plugin_fields.player_num - 1)
            player = plugin_fields.player_list[index]
            plugin_fields.server.rcon_query("botfollow %s" % player)

    def add_occupation(self, player: str):
        if self.online and self.running:
            self.occupied_players.push(player)
            plugin_fields.server.rcon_query('botfollow %s' % player)
            plugin_fields.server.broadcast('玩家 %s 临时获得了直播视角的控制权' % player)

    def copy(self):
        bot = LiveBotController()
        bot.mode = self.mode
        bot.occupied_players = self.occupied_players
        bot.online = self.online
        bot.running = self.running
        bot.time_since_last_tp = self.time_since_last_tp
        return bot


# -------------------------------------------
class Fields:
    def __init__(self) -> None:
        self.server = None
        self.bot = LiveBotController()
        self.player_num = 0
        self.player_list = []
        self.landscape_num = 0
        self.landscapes = []
        self.player_pattern = None


plugin_fields = Fields()


# -------------------------------------------
def load_config(server: ServerInterface):
    global config
    try:
        config = {}
        with open(CONFIG_PATH) as file:
            conf_yaml = yaml.load(file, Loader=yaml.Loader)  # idk why CLoader doesn't work
            for key in default_config.keys():
                config[key] = conf_yaml[key]
            server.logger.info('Config file loaded')
    except Exception as e:
        server.logger.warning('fail to read config file: %s, using default config' % e)
        config = default_config.copy()
        with open(CONFIG_PATH, 'w') as file:
            yaml.dump(default_config, file)


def load_landscape(server: ServerInterface):
    global plugin_fields
    try:
        with open(LANDSCAPE_PATH, 'r') as file:
            plugin_fields.landscapes = []
            plugin_fields.landscape_num = 0
            for line in file:
                plugin_fields.landscapes.append(str.removesuffix(line, '\n'))
                plugin_fields.landscape_num += 1
            server.logger.info('Landscape file loaded')
    except FileNotFoundError as e:
        server.logger.warning('fail to read landscape file: %s, creating it automatically.' % e)
        with open(LANDSCAPE_PATH, 'w') as file:
            pass


def build_command(server: ServerInterface):
    # register help message
    server.register_help_message(PREFIX, "Control the livebot")
    node = Literal(PREFIX).runs(occupy)
    server.register_command(node)
    # server.register_command(Literal('!!test').runs(dump))


@new_thread('LiveBotController_checkRcon')
def check_rcon():
    global plugin_fields
    time.sleep(1)
    # plugin_fields.server.logger.info('testing RCON...\n')
    if plugin_fields.server.is_server_startup() and not plugin_fields.server.is_rcon_running():
        cast('no_rcon')
        plugin_fields.server.unload_plugin(PLUGIN_METADATA['id'])


@new_thread('UpdatePlayer')
def update_player_list(server: ServerInterface):
    global plugin_fields
    query = server.rcon_query('list')
    match = re.match(LIST_PATTERN, query)
    if match:
        plugin_fields.player_num = int(match.group(1))
        plugin_fields.player_list = re.split(',\s', match.group(3))
        for player in plugin_fields.player_list:
            if plugin_fields.player_pattern is None:
                break
            if re.fullmatch(plugin_fields.player_pattern, player) is not None:
                plugin_fields.server.logger.info('remove %s' % player)
                plugin_fields.player_list.remove(player)
                plugin_fields.player_num -= 1


@new_thread('UpdatePlayer')
def update_bot_state(server: ServerInterface):
    global plugin_fields
    query = server.rcon_query('botstate')
    match = re.match(STATE_PATTERN, query)
    if match:
        if plugin_fields.bot.online and match.group(1) == 'Offline':
            plugin_fields.bot.online = False
        elif not (plugin_fields.bot.online or match.group(1) == 'Offline'):
            plugin_fields.bot.online = True


def occupy(cmd_src: CommandSource):
    global plugin_fields
    if cmd_src.is_player:
        plugin_fields.bot.add_occupation(cmd_src.player)
    else:
        cast('console_warning')


def cast(event: str):
    global plugin_fields
    server = plugin_fields.server
    {
        'bot_start': lambda: server.logger.info('Bot started.'),
        'bot_stop': lambda: server.logger.info('Bot stopped.'),
        'console_warning': lambda: server.logger.warning('Console command is not supported.'),
        'no_rcon': lambda: server.logger.warning('RCON is not enabled, unloading plugin.'),
        'thing': lambda: server.logger.info('something\n')
    }[event]()


def dump(cmd_src: CommandSource):
    cmd_src.reply('plugin_fields:' + plugin_fields.player_list.__str__() + '_%d' % plugin_fields.player_num)
    cmd_src.reply('landscape:' + plugin_fields.landscapes.__str__() + '_%d' % plugin_fields.landscape_num)
    cmd_src.reply('bot: mode: ' + plugin_fields.bot.mode.__str__() +
                  ', running: ' + plugin_fields.bot.running.__str__() +
                  ', online: ' + plugin_fields.bot.online.__str__() +
                  ', list: ' + plugin_fields.bot.occupied_players.players.__str__() +
                  ', count: ' + plugin_fields.bot.occupied_players.size.__str__())
    pass


# -------------------------------------------

def on_load(server: ServerInterface, old_module):
    global plugin_fields, config
    if old_module is not None:
        plugin_fields = old_module.plugin_fields
        plugin_fields.bot = old_module.plugin_fields.bot.copy()
    plugin_fields.server = server
    load_config(server)
    load_landscape(server)
    check_rcon()
    if config['excludedPrefix'] != '' or config['excludedSuffix'] != '':
        plugin_fields.player_pattern = re.compile(
            r'(' + config['excludedPrefix'] + r')' +
            r'\w+' +
            r'(' + config['excludedSuffix'] + r')'
        )
    else:
        plugin_fields.player_pattern = None
    build_command(server)
    if server.is_server_startup():
        plugin_fields.bot.start()


def on_unload(server: ServerInterface):
    global plugin_fields
    plugin_fields.bot.running = False


def on_server_stop(server: ServerInterface, code: int):
    global plugin_fields
    plugin_fields.bot.running = False


def on_server_startup(server: ServerInterface):
    global plugin_fields
    check_rcon()
    plugin_fields.bot.start()


def on_player_left(server: ServerInterface, player):
    update_player_list(server)
    update_bot_state(server)


def on_player_joined(server: ServerInterface, player: str, info: Info):
    update_player_list(server)
    update_bot_state(server)
