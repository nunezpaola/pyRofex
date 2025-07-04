# -*- coding: utf-8 -*-
"""
    pyRofex.websocket_client

    Defines a Websocket Client that connect to ROFEX Websocket API.
"""
import threading
import time
import logging

import websocket
import simplejson

from ..components import globals
from ..components import messages
from ..components.enums import TimeInForce
from ..components.enums import OrderType
from ..components.exceptions import ApiException


class WebSocketClient():
    """ Websocket Client that connect to Primary Websocket API.

    This client used a websocket implementation of the library websocket_client.

    - For more references of websocket_client library go to: https://pypi.org/project/websocket_client
    - For more information about the API go to: https://apihub.primary.com.ar/assets/docs/Primary-API.pdf

    """

    def __init__(self, environment):
        """ Initialization of the client.

        Create and initialize instance variables for the client.

        :param environment: the environment that will be associated with the client.
        :type environment: Environment (Enum)
        """

        # Environment associated with Client
        self.environment = globals.environment_config[environment]

        # Handlers for incoming messages
        self.market_data_handlers = []
        self.order_report_handlers = []
        self.error_handlers = []
        self.exception_handler = None

        # Connection related variables
        self.ws_connection = None
        self.ws_thread = None
        self.connected = False
        
        # Reconnection control variables
        self.auto_reconnect = True
        self.max_reconnect_attempts = 3
        self.reconnect_delay = 2
        
        # Subscription tracking for reconnection
        self.active_subscriptions = {
            'market_data': [],      # Lista de suscripciones de market data
            'order_report': []      # Lista de suscripciones de order report
        }

    def add_market_data_handler(self, handler):
        """ Adds a new Market Data handler to the handlers list.

        :param handler: function that is going to be call when a new Market Data Message is received.
        :type handler: callable.
        """
        if handler not in self.market_data_handlers:
            self.market_data_handlers.append(handler)

    def remove_market_data_handler(self, handler):
        """ Removes the Market Data handler from the handler list.

        :param handler: function to be removed from the handler list.
        :type handler: callable.
        """
        if handler in self.market_data_handlers:
            self.market_data_handlers.remove(handler)

    def add_order_report_handler(self, handler):
        """ Adds a new Order Report handler to the handlers list.

        :param handler: function that is going to be call when a new Order Report Message is received.
        :type handler: callable.
        """
        if handler not in self.order_report_handlers:
            self.order_report_handlers.append(handler)

    def remove_order_report_handler(self, handler):
        """ Removes the Order Report handler from the handler list.

        :param handler: function to be removed from the handler list.
        :type handler: callable.
        """
        if handler in self.order_report_handlers:
            self.order_report_handlers.remove(handler)

    def add_error_handler(self, handler):
        """ Adds a new Error handler to the handlers list.

        :param handler: function that is going to be call when a new Error Message is received.
        :type handler: callable.
        """
        if handler not in self.error_handlers:
            self.error_handlers.append(handler)

    def remove_error_handler(self, handler):
        """ Removes the Error handler from the handler list.

        :param handler: function to be removed from the handler list.
        :type handler: callable.
        """
        if handler in self.error_handlers:
            self.error_handlers.remove(handler)

    def set_exception_handler(self, handler):
        """ Sets the Exception Handler.

        :param handler: function called when Exception is raised.
        :type handler: callable.
        """
        self.exception_handler = handler

    def connect(self):
        """ Start a new websocket connection with ROFEX API.

        Create an instance WebSocketApp using the environment
        It will create a new thread that is going to be listening new incoming messages.
        """

        if self.ws_thread is not None and self.ws_thread.is_alive():
            # To avoid connecting again if the ws thread is alive
            return

        headers = {'X-Auth-Token:{token}'.format(token=self.environment["token"])}
        self.ws_connection = websocket.WebSocketApp(self.environment["ws"],
                                                    on_message=self.on_message,
                                                    on_error=self.on_error,
                                                    on_close=self.on_close,
                                                    on_open=self.on_open,
                                                    header=headers)

        # Create a thread and target it to the run_forever function, then start it.
        self.ws_thread = threading.Thread(target=self.ws_connection.run_forever,
                                          kwargs={"ping_interval": self.environment["heartbeat"],
                                                  "sslopt": self.environment["ssl_opt"]})
        self.ws_thread.start()

        # Wait 5 sec to establish the connection
        conn_timeout = 5
        while self.ws_thread.is_alive() \
                and (self.ws_connection.sock is None or not self.ws_connection.sock.connected) \
                and conn_timeout > 0:
            time.sleep(1)
            conn_timeout -= 1

        if self.ws_connection.sock is None or not self.ws_connection.sock.connected:
            self.on_exception(ApiException("Connection could not be established."))

    def on_message(self, ws, message):
        """ Called when a new message is received through the connection.

        :param message: message received.
        :type message: str
        """
        try:
            # Transform the JSON string message to a dict.
            msg = simplejson.loads(message)

            # Checks if it is an error message
            if 'status' in msg and msg['status'] == 'ERROR':
                for handler in self.error_handlers:
                    handler(msg)
            elif 'type' in msg:
                # extract the message type.
                msg_type = msg['type'].upper()

                # Checks message type and call the correct handlers
                if msg_type == 'MD':
                    for handler in self.market_data_handlers:
                        handler(msg)
                elif msg_type == 'OR':
                    for handler in self.order_report_handlers:
                        handler(msg)
                else:
                    msg_type_not_supported = "Websocket: Message Type not Supported. Message: {msg}"
                    for handler in self.error_handlers:
                        handler(msg_type_not_supported.format(msg=msg))
            else:
                msg_not_supported = "Websocket: Message Supported. Message: {msg}"
                for handler in self.error_handlers:
                    handler(msg_not_supported.format(msg=msg))

        except Exception as e:
            self.on_exception(e)

    def on_error(self, ws, exception):
        """ Called when an error occurred within the connection.

        :param exception: exception raised.
        :type exception: exception object
        """
        self.ws_connection.close()
        self.on_exception(exception)

    def on_exception(self, exception):
        """Called when an exception occurred within the client.

        :param exception: exception raised.
        :type exception: exception object
        """
        if self.exception_handler is not None:
            self.exception_handler(exception)

    def on_close(self, ws, close_status_code, close_msg):
        """Called when the connection is closed."""
        logging.log(logging.INFO, f"connection closed. code: {close_status_code}. message: {close_msg}")
        self.connected = False

        if close_status_code == 1008:
            logging.warning("Conexión cerrada con código 1008. Intentando reconexión automática...")
            # Ejecutar reconexión en un hilo separado para no bloquear
            reconnection_thread = threading.Thread(target=self._attempt_reconnection, daemon=True)
            reconnection_thread.start()

    def on_open(self, ws):
        """ Called when the connection is opened.
        """
        self.connected = True

    def close_connection(self):
        """ Close the connection.
        """
        self.ws_connection.close()

    def is_connected(self):
        """ Checks if the client is connected to the API.

        :return: True: if it is connected. False: if it is not connected.
        :rtype: boolean.
        """
        return self.connected

    def market_data_subscription(self, tickers, entries, market, depth):
        """ Creates and sends new Market Data Subscription Message through the connection.

        :param tickers: List of the tickers to subscribe.
        :type tickers: list of str
        :param entries: List of market data entries that want to be received.
        Example: [MarketDataEntry.BIDS, MarketDataEntry.OFFERS]
        :type entries: List of MarketDataEntry (Enum).
        :param market: Market id associated to the tickers.
        :type market: Market (Enum).
        :param depth: Market depth to received. default: 1 (top of book)
        :type depth: int
        """

        # Store subscription info for reconnection
        subscription_info = {
            'tickers': tickers,
            'entries': entries,
            'market': market,
            'depth': depth
        }
        
        # Check if this subscription already exists
        existing_sub = None
        for sub in self.active_subscriptions['market_data']:
            if (sub['tickers'] == tickers and 
                sub['market'] == market and 
                sub['depth'] == depth and
                sub['entries'] == entries):
                existing_sub = sub
                break
        
        # Add or update subscription
        if not existing_sub:
            self.active_subscriptions['market_data'].append(subscription_info)
            logging.info(f"Guardada suscripción MD: {tickers} en {market.value}")

        # Iterates through the tickers list and creates a new list of Instrument String using the INSTRUMENT Template.
        # Then create a comma separated string with the instruments in the list.
        instruments = [messages.INSTRUMENT.format(ticker=ticker, market=market.value) for ticker in tickers]
        instruments_string = ",".join(instruments)

        # Iterates through the entries list and creates a new list with the entry values.
        # Then creates a comma separated string with the entries in the list. Sample Output: '"BI","OF"'
        entries = [messages.DOUBLE_QUOTES.format(item=entry.value) for entry in entries]
        entries_string = ",".join(entries)

        # Creates a Market Data Subscription Message using the Template.
        message = messages.MARKET_DATA_SUBSCRIPTION.format(depth=depth,
                                                           entries=entries_string,
                                                           symbols=instruments_string)

        # Send the message through the connection.
        self.ws_connection.send(message)

    def order_report_subscription(self, account, snapshot):
        """ Creates and sends new Order Report Subscription Message through the connection.

        :param account: account that will be send in the message.
        :type account: str.
        :param snapshot: True: old Order Reports won't be received; False: old Order Report will be received.
        :type snapshot: boolean.
        """

        # Store subscription info for reconnection
        subscription_info = {
            'account': account,
            'snapshot': snapshot
        }
        
        # Check if this subscription already exists
        existing_sub = None
        for sub in self.active_subscriptions['order_report']:
            if sub['account'] == account and sub['snapshot'] == snapshot:
                existing_sub = sub
                break
        
        # Add subscription if it doesn't exist
        if not existing_sub:
            self.active_subscriptions['order_report'].append(subscription_info)
            logging.info(f"Guardada suscripción OR: {account}")

        # Create an Order Subscription message using the Template and the parameters.
        message = messages.ORDER_SUBSCRIPTION.format(a=account, snapshot=snapshot.__str__().lower())

        # Send the message through the connection.
        self.ws_connection.send(message)

    def cancel_order(self, client_order_id, proprietary):
        """ Creates and sends Cancel Order Message through the connection.

        The message will contain the client order id associated to the order.

        For more detailed information go to: https://apihub.primary.com.ar/assets/docs/Primary-API.pdf

        :param client_order_id: Client Order ID of the order.
        :type client_order_id: str
        :param proprietary: Proprietary of the order.
        :type proprietary: str
        """
        self.ws_connection.send(messages.CANCEL_ORDER.format(id=client_order_id, p=proprietary))

    def send_order(self, ticker, size, side, order_type,
                   account, price, time_in_force, market,
                   cancel_previous, iceberg, expire_date,
                   display_quantity, all_or_none, ws_client_order_id):
        """ Creates and sends a New Order Message through the connection.

        For more detailed information go to: https://apihub.primary.com.ar/assets/docs/Primary-API.pdf

        :param ticker: Instrument symbol to send in the request. Example: DLR/MAR23.
        :type ticker: str
        :param size: Order size.
        :type size: int
        :param side: Order side. Example: Side.BUY.
        :type side: Side (Enum).
        :param order_type: Order type. Example: OrderType.LIMIT.
        :type order_type: OrderType (Enum).
        :param account: Account to used.
        :type account: str
        :param price: Order price.
        :type price: float
        :param time_in_force: Order modifier that defines the active time of the order.
        :type time_in_force: TimeInForce (Enum).
        :param market: Market ID related to the instrument.
        :type market: Market (Enum).
        :param cancel_previous: True: cancels actives orders that match with the account, side and ticker.
        False: send the order without cancelling previous ones. Useful for replacing old orders.
        :type cancel_previous: boolean.
        :param iceberg: True: if it is an iceberg order. False: if it's not an iceberg order.
        :type iceberg: boolean.
        :param expire_date: Indicates the Expiration date for a GTD order. Example: 20170720.
        :type expire_date: str.
        :param display_quantity: Indicates the amount to be disclosed for GTD orders.
        :type display_quantity: int
        :param all_or_none: Fill all the order or none. Default False
        :type all_or_none: bool.
        :param ws_client_order_id: ID set by Client for orders. Default None
        :type ws_client_order_id: str.
        """

        opt_params = ""

        # Adds Optional Parameters
        if time_in_force is TimeInForce.GoodTillDate:
            opt_params = opt_params + messages.GOOD_TILL_DATE

        if iceberg:
            opt_params = opt_params + messages.ICEBERG

        if ws_client_order_id is not None:
            opt_params = opt_params + messages.WS_CLIENT_ORDER_ID

        if price is not None and order_type is OrderType.LIMIT:
            opt_params = opt_params + messages.PRICE

        opt_params = opt_params.format(price=price,
                                       iceberg=iceberg,
                                       expire_date=expire_date,
                                       display_quantity=display_quantity,
                                       wsClOrdID=ws_client_order_id)

        self.ws_connection.send(messages.SEND_ORDER.format(market=market.value,
                                                           ticker=ticker,
                                                           size=size,
                                                           side=side.value.upper(),
                                                           time_force=time_in_force.value.upper(),
                                                           account=account,
                                                           cancel_previous=cancel_previous,
                                                           all_or_none=all_or_none,
                                                           order_type=order_type.value,
                                                           optional_params=opt_params))

    def _attempt_reconnection(self):
        """Attempt to reconnect the WebSocket connection with exponential backoff."""
        if not self.auto_reconnect:
            return
            
        max_attempts = self.max_reconnect_attempts
        initial_delay = self.reconnect_delay
        attempt = 1
        delay = initial_delay
        
        while attempt <= max_attempts:
            try:
                logging.info(f"Intento de reconexión {attempt}/{max_attempts}...")
                
                # Wait before attempting reconnection
                time.sleep(delay)
                
                # First attempt: try simple reconnection without re-authentication
                if attempt == 1:
                    logging.info("Intentando reconexión simple (sin reautenticación)...")
                    self.connect()
                    
                    if self.is_connected():
                        logging.info("Reconexión simple exitosa.")
                        # Restore subscriptions after successful reconnection
                        time.sleep(1)  # Wait a moment for connection to stabilize
                        self._restore_subscriptions()
                        return
                else:
                    # Subsequent attempts: try re-authentication
                    logging.info("Intentando reconexión con reautenticación...")
                    
                    # Find the corresponding REST client and update token
                    for env_key, env_config in globals.environment_config.items():
                        if env_config.get('ws_client') == self:
                            rest_client = env_config.get('rest_client')
                            if rest_client:
                                rest_client.update_token()
                                logging.info("Token actualizado exitosamente.")
                                break
                    
                    # Try to reconnect with new token
                    self.connect()
                    
                    if self.is_connected():
                        logging.info("Reconexión con reautenticación exitosa.")
                        # Restore subscriptions after successful reconnection
                        time.sleep(1)  # Wait a moment for connection to stabilize
                        self._restore_subscriptions()
                        return
                
            except Exception as e:
                logging.error(f"Intento {attempt} falló: {e}")
            
            attempt += 1
            delay *= 2  # Exponential backoff
        
        # All attempts failed
        logging.error(f"Falló la reconexión después de {max_attempts} intentos.")
        if self.exception_handler:
            self.exception_handler(ApiException(f"Reconexión automática falló después de {max_attempts} intentos"))

    def set_auto_reconnect(self, enabled=True, max_attempts=3, delay=2):
        """Configure automatic reconnection parameters.
        
        :param enabled: Enable or disable automatic reconnection
        :type enabled: bool
        :param max_attempts: Maximum number of reconnection attempts
        :type max_attempts: int
        :param delay: Initial delay between attempts in seconds
        :type delay: int
        """
        self.auto_reconnect = enabled
        self.max_reconnect_attempts = max_attempts
        self.reconnect_delay = delay
        logging.info(f"Auto-reconexión configurada: habilitada={enabled}, max_intentos={max_attempts}, delay={delay}s")

    def disable_auto_reconnect(self):
        """Disable automatic reconnection."""
        self.auto_reconnect = False
        logging.info("Auto-reconexión deshabilitada")

    def _restore_subscriptions(self):
        """Restore all active subscriptions after reconnection."""
        try:
            # Restore market data subscriptions
            for sub in self.active_subscriptions['market_data']:
                logging.info(f"Restaurando suscripción MD: {sub['tickers']} en {sub['market'].value}")
                
                # Create the subscription message
                instruments = [messages.INSTRUMENT.format(ticker=ticker, market=sub['market'].value) 
                             for ticker in sub['tickers']]
                instruments_string = ",".join(instruments)
                
                entries = [messages.DOUBLE_QUOTES.format(item=entry.value) for entry in sub['entries']]
                entries_string = ",".join(entries)
                
                message = messages.MARKET_DATA_SUBSCRIPTION.format(
                    depth=sub['depth'],
                    entries=entries_string,
                    symbols=instruments_string
                )
                
                self.ws_connection.send(message)
                time.sleep(0.1)  # Small delay between subscriptions
            
            # Restore order report subscriptions
            for sub in self.active_subscriptions['order_report']:
                logging.info(f"Restaurando suscripción OR: {sub['account']}")
                
                message = messages.ORDER_SUBSCRIPTION.format(
                    a=sub['account'], 
                    snapshot=sub['snapshot'].__str__().lower()
                )
                
                self.ws_connection.send(message)
                time.sleep(0.1)  # Small delay between subscriptions
            
            if (len(self.active_subscriptions['market_data']) > 0 or 
                len(self.active_subscriptions['order_report']) > 0):
                logging.info("Todas las suscripciones han sido restauradas exitosamente.")
            
        except Exception as e:
            logging.error(f"Error al restaurar suscripciones: {e}")

    def clear_subscriptions(self):
        """Clear all stored subscriptions."""
        self.active_subscriptions = {
            'market_data': [],
            'order_report': []
        }
        logging.info("Todas las suscripciones guardadas han sido limpiadas.")

    def get_active_subscriptions(self):
        """Get current active subscriptions."""
        return self.active_subscriptions.copy()
