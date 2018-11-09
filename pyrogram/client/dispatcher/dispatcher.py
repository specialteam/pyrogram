# Pyrogram - Telegram MTProto API Client Library for Python
# Copyright (C) 2017-2018 Dan Tès <https://github.com/delivrance>
#
# This file is part of Pyrogram.
#
# Pyrogram is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Pyrogram is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with Pyrogram.  If not, see <http://www.gnu.org/licenses/>.

import asyncio
import logging
from collections import OrderedDict

from pyrogram.api import types
from ..ext import utils
from ..handlers import CallbackQueryHandler, MessageHandler, DeletedMessagesHandler, UserStatusHandler, RawUpdateHandler

log = logging.getLogger(__name__)


class Dispatcher:
    NEW_MESSAGE_UPDATES = (
        types.UpdateNewMessage,
        types.UpdateNewChannelMessage
    )

    EDIT_MESSAGE_UPDATES = (
        types.UpdateEditMessage,
        types.UpdateEditChannelMessage
    )

    DELETE_MESSAGE_UPDATES = (
        types.UpdateDeleteMessages,
        types.UpdateDeleteChannelMessages
    )

    CALLBACK_QUERY_UPDATES = (
        types.UpdateBotCallbackQuery,
        types.UpdateInlineBotCallbackQuery
    )

    MESSAGE_UPDATES = NEW_MESSAGE_UPDATES + EDIT_MESSAGE_UPDATES

    UPDATES = None

    def __init__(self, client, workers: int):
        self.client = client
        self.workers = workers

        self.update_worker_tasks = []
        self.updates = asyncio.Queue()
        self.groups = OrderedDict()

        Dispatcher.UPDATES = {
            Dispatcher.MESSAGE_UPDATES:
                lambda upd, usr, cht: (utils.parse_messages(self.client, upd.message, usr, cht), MessageHandler),

            Dispatcher.DELETE_MESSAGE_UPDATES:
                lambda upd, usr, cht: (utils.parse_deleted_messages(upd), DeletedMessagesHandler),

            Dispatcher.CALLBACK_QUERY_UPDATES:
                lambda upd, usr, cht: (utils.parse_callback_query(self.client, upd, usr), CallbackQueryHandler),

            (types.UpdateUserStatus,):
                lambda upd, usr, cht: (utils.parse_user_status(upd.status, upd.user_id), UserStatusHandler)
        }

        Dispatcher.UPDATES = {key: value for key_tuple, value in Dispatcher.UPDATES.items() for key in key_tuple}

    async def start(self):
        for i in range(self.workers):
            self.update_worker_tasks.append(
                asyncio.ensure_future(self.update_worker())
            )

        log.info("Started {} UpdateWorkerTasks".format(self.workers))

    async def stop(self):
        for i in range(self.workers):
            self.updates.put_nowait(None)

        for i in self.update_worker_tasks:
            await i

        self.update_worker_tasks.clear()

        log.info("Stopped {} UpdateWorkerTasks".format(self.workers))

    def add_handler(self, handler, group: int):
        if group not in self.groups:
            self.groups[group] = []
            self.groups = OrderedDict(sorted(self.groups.items()))

        self.groups[group].append(handler)

    def remove_handler(self, handler, group: int):
        if group not in self.groups:
            raise ValueError("Group {} does not exist. Handler was not removed.".format(group))

        self.groups[group].remove(handler)

    def update_worker(self):
        while True:
            update = self.updates.get()

            if update is None:
                break

            try:
                users = {i.id: i for i in update[1]}
                chats = {i.id: i for i in update[2]}
                update = update[0]

                parser = Dispatcher.UPDATES.get(type(update), None)

                if parser is None:
                    continue

                update, handler_type = parser(update, users, chats)
                tasks = []

                for group in self.groups.values():
                    for handler in group:
                        args = None

                        if isinstance(handler, RawUpdateHandler):
                            args = (update, users, chats)
                        elif isinstance(handler, handler_type):
                            if handler.check(update):
                                args = (update,)

                        if args is None:
                            continue

                        try:
                            tasks.append(handler.callback(self.client, *args))
                        except Exception as e:
                            log.error(e, exc_info=True)
                        finally:
                            break

                await asyncio.gather(*tasks)
            except Exception as e:
                log.error(e, exc_info=True)
