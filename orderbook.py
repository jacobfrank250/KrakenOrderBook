import sys
import json
import signal
from websocket import create_connection
import threading
import queue 
import tkinter
import tkinter.messagebox


class OrderBookThread(threading.Thread):
    def __init__(self, api_symbol, api_depth, gui_q, target = None, name = None):
        super(OrderBookThread,self).__init__()
        
        self.api_feed = "book"
        self.api_domain = "wss://ws.kraken.com/"
        self.api_book = {"bid":{}, "ask":{}}
        
        self.api_depth = api_depth
        self.api_symbol = api_symbol

        self.gui_q = gui_q

        self.target = target
        self.name = name

        self.stop = False

        signal.signal(signal.SIGALRM, self.alarmfunction)
    
    def close(self):
        self.stop = True
        self.join()
    
    def run(self):
        try:
            ws = create_connection(self.api_domain)
        except Exception as error:
            print("WebSocket connection failed (%s)" % error)
            sys.exit(1)

        api_data = '{"event":"subscribe", "subscription":{"name":"%(feed)s", "depth":%(depth)s}, "pair":["%(symbol)s"]}' % {"feed":self.api_feed, "depth":self.api_depth, "symbol":self.api_symbol}

        try:
            ws.send(api_data)
        except Exception as error:
            print("Feed subscription failed (%s)" % error)
            ws.close()
            sys.exit(1)

        while not self.stop:
            try:
                api_data = ws.recv()
            except KeyboardInterrupt:
                ws.close()
                sys.exit(0)
            except Exception as error:
                print("WebSocket message failed (%s)" % error)
                ws.close()
                sys.exit(1)
            api_data = json.loads(api_data)
            if type(api_data) == list:
                # Snapshot Message
                if "as" in api_data[1]:
                    self.api_update_book("ask", api_data[1]["as"])
                    self.api_update_book("bid", api_data[1]["bs"])
                    signal.alarm(1)
                # Update Message
                elif "a" in api_data[1] or "b" in api_data[1]:
                    for x in api_data[1:len(api_data[1:])-1]:
                        if "a" in x:
                            self.api_update_book("ask", x["a"])
                        elif "b" in x:
                            self.api_update_book("bid", x["b"])

        ws.close()
        sys.exit(1)

    def alarmfunction(self,signalnumber, frame):
        signal.alarm(1)
        self.api_output_book()

    def dicttofloat(self,keyvalue):
            return float(keyvalue[0])

    # This method displays order book
    def api_output_book(self):
        bid = sorted(self.api_book["bid"].items(), key=self.dicttofloat, reverse=True)
        ask = sorted(self.api_book["ask"].items(), key=self.dicttofloat)
        
        topAsks = []
        topBids = []
        print("Bid\t\t\t\t\t\tAsk")
        for x in range(int(self.api_depth)):
            print("%(bidprice)s (%(bidvolume)s)\t\t\t\t%(askprice)s (%(askvolume)s)" % {"bidprice":bid[x][0], "bidvolume":bid[x][1], "askprice":ask[x][0], "askvolume":ask[x][1]})
            topBids.append(bid[x][0])
            topAsks.append(ask[x][0])

        msgForQ = {"topAsks": topAsks,"topBids":topBids}
        try:
            # Put orderbook snap shot in queue for receiver thread
            self.gui_q.put(msgForQ,block=False)
        except queue.Full:
            # Gui thread has not removed order book snapshot message yet from queue–do not place in another .
            pass
    
    # This method updates the order book
    def api_update_book(self,side, data):
        for x in data:
            price_level = x[0]
            if float(x[1]) != 0.0:
                # Update book for this price
                self.api_book[side].update({price_level:float(x[1])})
            else:
                # Size is zero so remove from book (if its in the book)
                if price_level in self.api_book[side]:
                    self.api_book[side].pop(price_level)
        
        # Re-sort book
        if side == "bid":
            self.api_book["bid"] = dict(sorted(self.api_book["bid"].items(), key=self.dicttofloat, reverse=True)[:int(self.api_depth)])
        elif side == "ask":
            self.api_book["ask"] = dict(sorted(self.api_book["ask"].items(), key=self.dicttofloat)[:int(self.api_depth)])

class GuiThread(tkinter.Frame):
        def __init__(self,parent,in_q,levels):
            tkinter.Frame.__init__(self, parent)
            
            # Queue shared by OrderBookConsumer (gui thread) and OrderBookProducer (websocket thread)
            self.in_q = in_q
            
            # Amount of prices to display in the order book gui
            self.levels = levels
            
            # gui window object
            self.parent = parent
            
            # gui window title
            self.parent.title("Kraken BTC/USD")
           
            # This list will hold the stringvars attached to each bid/asl label 
            self.bidTexts = []
            self.askTexts = []

            # This list will hold all the bid/asl labels. Each label is an item in the list displayed in the gui window
            self.bidLabels = []
            self.askLabels = []

            # Initialize the gui window ask list 
            self.createList(self.askLabels,self.askTexts,"red",self.levels)
            
            # Initialize the gui window bid list 
            self.createList(self.bidLabels,self.bidTexts,"green",self.levels)

            # Start continuous loop to refresh orderbook gui every millisecond
            self.parent.after(1,self.refreshBook)

        # Initialize our bid and ask lists
        def createList(self,labels,texts,textColor,n):
            for i in range(n):
                texts.append(tkinter.StringVar())
                texts[i].set("0.00")
                labels.append(tkinter.Label(self.parent,textvariable = texts[i],fg = textColor,bg="black"))
                labels[i].pack()
        
        # Call this every millisecond to update the order book view 
        def refreshBook(self):
            try:
                # Get (consume) data sent from orderbook thread
                data = self.in_q.get(block=False)
                # Update ask and bid list text
                self.updateList(data["topAsks"],data["topBids"])
            except queue.Empty:
                # No messages sent from orderbook thread
                pass
            finally:
                # Update order book view again in 1 millisecond
                self.parent.after(1,self.refreshBook)
        
        # This method updates ask and bid lists
        def updateList(self,asks,bids):
            for i,bid in enumerate(bids):
                self.bidTexts[i].set(bid)


            for i,ask in enumerate(asks):
                # List asks in reverse order
                self.askTexts[len(self.askTexts)-1-i].set(ask)


        def formatPrice(self,price):
            return '{0:.2f}'.format(price)

class OrderBookGui:
    def __init__(self):
        # Check Valid Input
        # if len(sys.argv) < 3:
        # 	print("Usage: %s symbol depth" % sys.argv[0])
        # 	print("Example: %s xbt/usd 10" % sys.argv[0])
        # 	sys.exit(1)
        
        # Create shared q
        self.q = queue.Queue(maxsize=1)

        # self.symbol = sys.argv[1].upper()
        # self.depth = sys.argv[2]
        self.symbol = 'XBT/USD'
        self.depth = '10'

        # Start Orderbook Thread (producer) 
        self.orderbook = OrderBookThread(self.symbol,self.depth,self.q,name = 'Producer')
        self.orderbook.start()

        # Start GUI thread

        # Create the GUI window
        self.root = tkinter.Tk()
        # Set background to black
        self.root.configure(bg='black')

        # Create GUI instannce
        self.consumer = GuiThread(self.root,self.q,int(self.depth))

         # Binds close window button to askTerminate
        self.root.protocol("WM_DELETE_WINDOW", self.askTerminate)
        # Start Gui loop (consumer thread)
        self.root.mainloop()

    # Function is called when user closes GUI window
    def askTerminate(self):
        if tkinter.messagebox.askokcancel("Quit", "You want to close the order book?"):
            self.root.destroy()
            self.orderbook.close()



if __name__ == '__main__':
    OrderBookGui()





        

