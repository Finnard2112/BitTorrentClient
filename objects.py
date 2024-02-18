import threading
import math
import struct
import time
import selectors
import utils
import traceback
from collections import deque

# This variable is used for toggling console debug messages
# Enabled means you can view object states and connections in the console
# *** KEEP DEBUG_MODE = False, Toggle the '-d' option if you want to view debug comments
DEBUG_MODE = False
CHOKE = 0
UNCHOKE = 1
INTERESTED = 2
NOT_INTERESTED = 3
HAVE = 4
BITFIELD = 5
REQUEST = 6
PIECE = 7
CANCEL = 8
PORT = 9

class handshake:
    def __init__(self):
        self._pstrlen: None
        self._pstr: None
        self._reserved: None
        self._info_hash: None
        self._peer_id: None

    @property
    def pstrlen(self):
        return self._pstrlen
    
    @property 
    def pstr(self):
        return self._pstr

    @property 
    def reserved(self):
        return self._reserved
    
    @property 
    def info_hash(self):
        return self._info_hash
    
    @property 
    def peer_id(self):
        return self._peer_id
    
    @info_hash.setter
    #just sets the entire handshake client->peer
    def info_hash(self, value):
        value1, value2 = value
        self._pstrlen = len("BitTorrent protocol").to_bytes(1,byteorder='big')
        self._pstr = "BitTorrent protocol".encode('utf-8')
        self._reserved = b'\x00\x00\x00\x00\x00\x00\x00\x00'
        self._info_hash = value1
        self._peer_id = value2

#messages
# lenprefix - 4 bytes (big endian)
# msgid 1 btye (deciaml)
# len 0000 -> keep alive
# len 0001,id = 0 -> choke
# len 0001,id = 1 -> unchoke
# len 0001,id = 2 -> interested
# len 0001,id = 3 -> not interested
# len 0005,id = 4 -> have: payload is zero-based index of a piece that has been successfully downloaded and verified by the hash
# len 0001 + x,id = 5 -> bitfieled, x is length of bitfield
# len 0013,id = 6 -> request: payload is of form <index><begin><length>, all integers, index->zero-based piece index,begin->zero based byte offset in the piece,length-> requested length
# len 0009 + x,id = 7 -> piece: payload is of form <index><begin><block>, all integers, index-> zero-based piece index, begin-> zero based byte offset in the piece, block-> block of data, subset of piece (index)
# len 0013,id = 8 -> cancel: cancel block requests
# len 0003,id = 9 -> port: 
# payload - msg dependent
class messages:
    def __init__(self):
        self._lenprefix: None
        self._msgid: None
        self._payload: None
        self._fullMessage = None
    @property
    def lenprefix(self):
        return self._lenprefix
    @property
    def msgid(self):
        return self._msgid
    @property
    def payload(self):
        return self._payload
    @property
    def fullMessage(self):
        return self._fullMessage
    
    def keepAlive(self):
        self._lenprefix = b'\x00\x00\x00\x00'
        self._msgid = None
        self._payload = None
        self._fullMessage = (self._lenprefix)

    def choke(self):
        self._lenprefix = b'\x00\x00\x00\x01'
        self._msgid = b'\x00'
        self._payload = b''
        self._fullMessage = (self._lenprefix + self._msgid + self._payload)

    def unchoke(self):
        self._lenprefix = b'\x00\x00\x00\x01'
        self._msgid = b'\x01'
        self._payload = b''
        self._fullMessage = (self._lenprefix + self._msgid + self._payload)

    def interested(self):
        self._lenprefix = b'\x00\x00\x00\x01'
        self._msgid = b'\x02'
        self._payload = b''
        self._fullMessage = (self._lenprefix + self._msgid + self._payload)
    
    def notinterested(self):
        self._lenprefix = b'\x00\x00\x00\x01'
        self._msgid = b'\x03'
        self._payload = b''
        self._fullMessage = (self._lenprefix + self._msgid + self._payload)

    def have(self,value):
        self._lenprefix = b'\x00\x00\x00\x05'
        self._msgid = b'\x04'
        self._payload = value
        self._fullMessage = (self._lenprefix + self._msgid + self._payload)

    def request(self, value):
        index, begin, length = value 
        self._lenprefix = struct.pack(">I", 13)
        self._msgid = b'\x06'
        self._payload = struct.pack(">III", index, begin, length)
        self._fullMessage = (self._lenprefix + self._msgid + self._payload)

    def piece(self, value):
        length, index, begin, block = value 
        self._lenprefix = struct.pack(">I", 9+length)
        self._msgid = b'\x07'
        self._payload = struct.pack(f">II{length}B", index, begin, block)
        self._fullMessage = (self._lenprefix + self._msgid + self._payload)

class trackerScrapeMsg:
    def __init__(self):
        self._complete = None
        self._downloaded = None
        self._incomplete = None
        self._name = None
    
    @property
    def complete(self):
        return self._complete

    @complete.setter
    def complete(self, value):
        self._complete = value

    @property
    def downloaded(self):
        return self._downloaded

    @downloaded.setter
    def downloaded(self, value):
        self._downloaded = value

    @property
    def incomplete(self):
        return self._incomplete

    @incomplete.setter
    def incomplete(self, value):
        self._incomplete = value

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

class peer:
    def __init__(self):
        self._peerId = None
        self._peerAddr = None
        self._peerPort = None
        self._peerChoked = None
        self._peerInterested = None
        self._amChoking = None
        self._amInterested = None
        self._peerBitfield = None
        self._connection = None
        self._trackerInfo = None
        self._lock = None
        self._is_connected = None
        self._max_pipeline = None
        self._curr_reqs_in_progress = None
        self._cancelled_request = None
        self._last_data_downloaded = None
        self._cur_data_downloaded = None
        self._last_message_received = None
        self._isAlive = None

    @property
    def peerId(self):
        return self._peerId
    @property
    def peerAddr(self):
        return self._peerAddr
    @property
    def peerPort(self):
        return self._peerPort
    @property
    def connection(self):
        return self._connection

    @property
    def is_connected(self):
        return self._is_connected
    
    @is_connected.setter
    def is_connected(self, value):
        self._is_connected = value

    @property
    def peerChoked(self):
        return self._peerChoked
    
    @peerChoked.setter
    def peerChoked(self, value):
        self._peerChoked = value

    @property
    def peerInterested(self):
        return self._peerInterested
    
    @peerInterested.setter
    def peerInterested(self, value):
        self._peerInterested = value

    @property
    def amChoking(self):
        return self._amChoking
    
    @amChoking.setter
    def amChoking(self, value):
        self._amChoking = value

    @property
    def amInterested(self):
        return self._amInterested
    
    @amInterested.setter
    def amInterested(self, value):
        self._amInterested = value
    
    @property
    def peerBitfield(self):
        return self._peerBitfield
    
    @peerBitfield.setter
    def peerBitfield(self, value):
        self._peerBitfield = value

    @property
    def cancelled_request(self):
        return self._cancelled_request
    
    @cancelled_request.setter
    def cancelled_request(self, value):
        self._cancelled_request = value

    @property
    def last_data_downloaded(self):
        return self._last_data_downloaded
    
    @last_data_downloaded.setter
    def last_data_downloaded(self, value):
        self._last_data_downloaded = value

    @property
    def cur_data_downloaded(self):
        return self._cur_data_downloaded
    
    @cur_data_downloaded.setter
    def cur_data_downloaded(self, value):
        self._cur_data_downloaded = value

    @property
    def last_message_received(self):
        return self._last_message_received
    
    @last_message_received.setter
    def last_message_received(self, value):
        self._last_message_received = value

    @property
    def isAlive(self):
        return self._isAlive


    
    
    @peerId.setter
    def peerId(self,value):
        ip,port,id,bitfield,connection, trackerInfo = value
        self._peerAddr = ip
        self._peerPort = port
        self._peerId = id
        self._peerBitfield = bitfield # Note: do not rely on length of stored bitfield to be == to length of pieces
        self._connection = connection
        self._trackerInfo = trackerInfo
        self._lock = threading.Lock() # Avoid calling send/recv at the same time and editing 
        self._is_connected = True
        # A choked peer is not allowed to request any pieces from the other peer.
        self._peerChoked = True
        # Indicates that the peer is not interested in requesting pieces.
        self._peerInterested = False
        # Indicates whether I'm interested in/choking them.
        self._amChoking = True
        self._amInterested = False
        self._max_pipeline = 5
        self._curr_reqs_in_progress = 0
        self._last_data_downloaded = 0
        self._cur_data_downloaded = 0
        self._isAlive = True
        self._last_message_received = time.time()
        
    def run_main_logic(self):
        try:
            # Start listening and downloading threads
            listenThread = threading.Thread(target= self.listen_for_messages)
            downloadThread = threading.Thread(target=self.download_pieces)
            listenThread.daemon = True
            downloadThread.daemon = True
            listenThread.start()
            downloadThread.start()

            # Send a alive message every ~2 minutes
            aliveMsgInterval = 0
            while trackerRequestMsg.left > 0:
                # Send interested/not interested based on bitfield
                self.determine_interested()
                now = time.time()
                aliveMsgInterval += now
                # Check if the last message received is longago
                if now - self.last_message_received > 120:
                    break
                time.sleep(3) 
                # Increment interval by 3 (because we slept by 3)
                aliveMsgInterval += 3
                if aliveMsgInterval == 124:
                    aliveMsgInterval = 0
                    alive = messages()
                    alive.keepAlive()
                    self.send_message(alive)

            
        except Exception as e:
            if DEBUG_MODE:
                    print(f"exception for peer at: {self._peerAddr}\n")
                    print(e)
                    traceback.print_exc()
                    return
        finally:
            with self._lock:
                self._isAlive = False
            self._connection.close()


    # This will run as long as piece taken is not downloaded yet. Returns False if faulty piece, True if completed
    def _download_attempt(self, index, num_blocks):
        try:
            exception_raised = False
            if index not in piecesStatus:
                piecesStatus[index] = 0
            if index not in piecesCollection:
                piecesCollection[index] = {}
            while piecesStatus[index] != None and self.isAlive:   
                # Iterate over blocks at 16KB interval offsets         
                for i in [x*16000 for x in range(num_blocks)]:
                    # Check how many requests are in flight
                    if self._curr_reqs_in_progress < self._max_pipeline and self._curr_reqs_in_progress < num_blocks:
                    # Checking for blocks that haven't been downloaded
                        if (piecesCollection[index] is not None) and i not in piecesCollection[index]:
                            request = messages()
                            length = 16000
                            pieceLength = self._trackerInfo.pieceLength

                            # This is for edge case of last piece (may have smaller piece length than others)
                            isLastPiece = False
                            maxPieceIndex = max(piecesCollection)  
                            print("maxPieceIndex -", maxPieceIndex, "self._trackerInfo.length -", self._trackerInfo.length, 'self._trackerInfo.pieceLength', self._trackerInfo.pieceLength)
                            print("Modified piece length -", self._trackerInfo.length - (maxPieceIndex * pieceLength))
                            if self._trackerInfo.length - (maxPieceIndex * pieceLength) < self._trackerInfo.pieceLength:
                                print("KDHBLKDJBHLKDJHKFHFHLJFHLKJFHL")
                                isLastPiece = True
                                pieceLength = self._trackerInfo.length - (maxPieceIndex * pieceLength)

                            if (i > pieceLength):
                                continue
                            # if i is the index of the last block, calculate length of the last block
                            print("i + length = ", i + length, ", pieceLength -", pieceLength)
                            if (i + length) > pieceLength:    
                                print("HAHAHAHAHAHAHAHAHAHA")                      
                                length = pieceLength - i
                            # Make a request message with piece index, block index, and length of block
                            request.request((index, i, length))
                            # Send a request through our own socket
                            with self._lock:
                                self._connection.sendall(request.fullMessage)
                                self._curr_reqs_in_progress += 1
                                print("SENT REQUEST MESSAGE!!! -", self.peerAddr)
                                print("length -", length, "Piece Index -", index, "Block Index -", i)
                    else:
                        # If we have max reqs in progress, we wait for 2 seconds
                        time.sleep(2) 
                        break
                # If at some point, verifyHash determines that the piece is wrong (and pop the index) 
                if index not in piecesStatus:
                    # Return piece index to top of the workDeque
                    with workDequeMutex:
                        workDeque.appendleft(index)
                    return False
            # Whole piece is finished
            return True
        except Exception as e:
            exception_raised = True
            if DEBUG_MODE:
                print(f"exception for peer requesting pieces at: {self._peerAddr}\n")
                print(e)
                traceback.print_exc()

            return
        finally:
            if exception_raised:
                with workDequeMutex:
                    workDeque.appendleft(index)

        

                

    def download_pieces(self):
        while trackerRequestMsg.left > 0 and self.isAlive:
            time.sleep(2)
            print("Current requests in flight -", self._curr_reqs_in_progress)
            print("Peer ", self)
            print("How many left - ", trackerRequestMsg.left)

            if not self.peerChoked:
                for i in range(len(workDeque)):
                    if self._peerBitfield[workDeque[i]]:
                        index = workDeque[i]
                        with workDequeMutex:
                            workDeque.remove(index)
                        break
                # 16000B = 16KB = Max block length
                num_blocks = math.ceil(self._trackerInfo.pieceLength/16000)
                print("Number of blocks -", num_blocks)
                is_completed = self._download_attempt(index, num_blocks)   
                if not is_completed:
                    if DEBUG_MODE:
                        print(f"Download attempt for piece {index} at peer {self._peerID} failed")

        print("EXITED THREAD FOR DOWNLOADING PIECES")

    def determine_interested(self):
        interested = messages()
        contains_needed_pieces = False
        # Look at what pieces' indices we have left to do
        for x in workDeque:
            # If peer bitfield contains this index, we would be interested
            if self._peerBitfield[x]:
                contains_needed_pieces = True
                break

        # If peer contains something I need:
        # 1: I was not interested (I now need some piece and am interested again): send interested 
        # 2: I'm already interested (I've sent interested message before): do nothing
        # Else if peer doesn't have something I need:
        # 1: I'm already interested (I'm not interested anymore since peer doesn't have something i need): send not interested
        # 2: I was not interested (I'm still not interested): do nothing

        if contains_needed_pieces:
            if not self._amInterested:
                self._amInterested = True
                interested.interested()
                with self._lock:
                    self._connection.sendall(interested.fullMessage)
        else:
            if self._amInterested:
                interested.notinterested()
                with self._lock:
                    self._connection.sendall(interested.fullMessage)

    def listen_for_messages(self):

        #Using selector to avoid waiting for a recv forever even though download might be done
        selector = selectors.DefaultSelector()
        selector.register(self._connection, selectors.EVENT_READ)

        while trackerRequestMsg.left > 0 and self.isAlive:
            events = selector.select(timeout=5)

            # Timeout hit

            if not events:
                # if DEBUG_MODE:
                #     print("peer selector timeout reached.")
                pass

            # Data in peer socket

            else:
                with self._lock:
                    # Get message from socket 
                    message = utils.get_message_from_sock(self._connection)
                    if message is None:
                        raise Exception("Didn't get message from sock")
                    self.last_message_received = time.time()
                    # Indicate we processed 1 in flight request
                    if message[1] == PIECE:
                        self._curr_reqs_in_progress -= 1


                # Send the entire Msg to parsePeerMsg (could be optimised here)
                utils.parsePeerMsg(message, self._trackerInfo, self)


    def set_have(self, index):
        if index <= len(self._peerBitfield):
            self._peerBitfield.set(1, index)
    
    def send_message(self, message):
        # Wait for potential cancel
        time.sleep(1)
        #If current message sending out is a piece message
        if message.msgid == b'\x07':
            # If theres a request cancelled
            if self._cancelled_request is not None:
                # If current message sent == cancelled request
                msgBody = struct.unpack('>II', message.payload[:8])
                if msgBody[0] == self._cancelled_request[0] and msgBody[1]== self._cancelled_request[1]:
                    # Clear cancelled request and return
                    self._cancelled_request = None
                    return
        with self._lock:
            self._connection.sendall(message.fullMessage)


class trackerReqMsg:
    def __init__(self):
        self._infoHash = None  
        self._peerId = None
        self._port = None
        self._uploaded = None
        self._downloaded = None
        self._left = None
        self._compact = None
        self._noPeerId = None
        # NOT included in periodic msgs, but IS included in start, stopped, and completed 
        self._event = None
        # The following are optional
        self._ip = None           # Never gonna use; IP always going to come from client
        self._numwant = None      # Maybe used?; Num peers client wants to recv from tracker; if not given, default = 50 peers
        self._key = None          # Idk if ever used; used if client changes IP address to prove identity
        self._trackerfield = None # Idk if ever used; could be used to identify a specific tracker instance or session.
    
    @property
    def infoHash(self):
        return self._infoHash

    @infoHash.setter
    def infoHash(self, value):
        self._infoHash = value

    @property
    def peerId(self):
        return self._peerId

    @peerId.setter
    def peerId(self, value):
        self._peerId = value

    @property
    def port(self):
        return self._port

    @port.setter
    def port(self, value):
        self._port = value

    @property
    def uploaded(self):
        return self._uploaded

    @uploaded.setter
    def uploaded(self, value):
        self._uploaded = value

    @property
    def downloaded(self):
        return self._downloaded

    @downloaded.setter
    def downloaded(self, value):
        self._downloaded = value

    @property
    def left(self):
        return self._left

    @left.setter
    def left(self, value):
        self._left = value

    @property
    def compact(self):
        return self._compact

    @compact.setter
    def compact(self, value):
        self._compact = value

    @property
    def noPeerId(self):
        return self._noPeerId

    @noPeerId.setter
    def noPeerId(self, value):
        self._noPeerId = value

    @property
    def event(self):
        return self._event

    @event.setter
    def event(self, value):
        self._event = value

    @property
    def ip(self):
        return self._ip

    @ip.setter
    def ip(self, value):
        self._ip = value

    @property
    def numwant(self):
        return self._numwant

    @numwant.setter
    def numwant(self, value):
        self._numwant = value

    @property
    def key(self):
        return self._key

    @key.setter
    def key(self, value):
        self._key = value

    @property
    def trackerfield(self):
        return self._trackerfield

    @trackerfield.setter
    def trackerfield(self, value):
        self._trackerfield = value

class trackerRespMsg:
    def __init__(self, failureReason=None, warningMsg=None, interval=None, minInterval=None, trackerId=None, complete=None, incomplete=None, peers=list()):
        self._failureReason = failureReason
        self._warningMsg = warningMsg
        self._interval = interval               # Will always have this
        self._minInterval = minInterval         
        self._trackerId = trackerId
        self._complete = complete               # Will always have this
        self._incomplete = incomplete           # Will always have this
        self._peers = list()                     # Will always have this; (special) List of dictionaries w/ keys: 'peer id' (excluded if 'no_peer_id' requested), 'ip', 'port'
        # Note: 'downloaded' is not actually part of response msg and is rly part of scrape msg; however, to consolidate data, I am putting it here since most fields are identical
        self._downloaded = None
        # Note: The following are strictly for for UDP msgs
        self._action = None
        self._transactionId = None
        self._connectionId = list()
        self._numAnnounces = 0

    @property
    def numAnnounces(self):
        return self._numAnnounces

    @numAnnounces.setter
    def numAnnounces(self, value):
        self._numAnnounces = value

    @property
    def action(self):
        return self._action

    @action.setter
    def action(self, value):
        self._action = value

    @property
    def transactionId(self):
        return self._transactionId

    @transactionId.setter
    def transactionId(self, value):
        self._transactionId = value

    @property
    def connectionId(self):
        return self._connectionId

    @connectionId.setter
    def connectionId(self, value):
        self._connectionId = value

    @property
    def downloaded(self):
        return self._downloaded

    @downloaded.setter
    def downloaded(self, value):
        self._downloaded = value

    @property
    def failureReason(self):
        return self._failureReason

    @failureReason.setter
    def failureReason(self, value):
        self._failureReason = value

    @property
    def warningMsg(self):
        return self._warningMsg

    @warningMsg.setter
    def warningMsg(self, value):
        self._warningMsg = value

    @property
    def interval(self):
        return self._interval

    @interval.setter
    def interval(self, value):
        self._interval = value

    @property
    def minInterval(self):
        return self._minInterval

    @minInterval.setter
    def minInterval(self, value):
        self._minInterval = value

    @property
    def trackerId(self):
        return self._trackerId

    @trackerId.setter
    def trackerId(self, value):
        self._trackerId = value

    @property
    def complete(self):
        return self._complete

    @complete.setter
    def complete(self, value):
        self._complete = value

    @property
    def incomplete(self):
        return self._incomplete

    @incomplete.setter
    def incomplete(self, value):
        self._incomplete = value

    @property
    def peers(self):
        return self._peers

    @peers.setter
    def peers(self, value):
        self._peers = value

    def printState(self):
        if DEBUG_MODE:
            print("Tracker Response Message:")
            print("\t- failureReason =", self._failureReason)
            print("\t- warningMsg =", self._warningMsg)
            print("\t- interval =", self._interval)
            print("\t- minInterval =", self._minInterval)
            print("\t- trackerId =", self._trackerId)
            print("\t- complete =", self._complete)
            print("\t- incomplete =", self._incomplete)
            print("\t- peers: ")
            for peer in self._peers:
                if 'peer id' in peer:
                    print("\t\t- peer id =", peer['peer id'], ", ip =", peer['ip'], ", port =", peer['port'])
                else:
                    print("\t\t- ip =", peer['ip'], ", port =", peer['port'])
            print('')
            print("------------------------------------------------------------------")

class trackerInfo:
    def __init__(self):
        self._ip = None
        self._port = None
        self._encoding = None
        self._name = None
        self._length = None
        self._pieceLength = None
        self._pieces = None
        self._private = None

    @property
    def length(self):
        return self._length

    @length.setter
    def length(self, value):
        self._length = value

    @property
    def pieceLength(self):
        return self._pieceLength

    @pieceLength.setter
    def pieceLength(self, value):
        self._pieceLength = value

    @property
    def pieces(self):
        return self._pieces

    @pieces.setter
    def pieces(self, value):
        self._pieces = value

    @property
    def private(self):
        return self._private

    @private.setter
    def private(self, value):
        self._private = value
      
    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

    @property
    def ip(self):
        return self._ip

    @ip.setter
    def ip(self, value):
        self._ip = value

    @property
    def port(self):
        return self._port

    @port.setter
    def port(self, value):
        self._port = value

    @property
    def encoding(self):
        return self._encoding

    @encoding.setter
    def encoding(self, value):
        self._encoding = value

# Initializing messages here is the only way I can think of to have them shared between threads 
# SHARED THREAD MESSAGE OBJECTS & MUTEX LOCKS
trackerRequestMsg = trackerReqMsg()
trackerResponseMsg = trackerRespMsg()
trackerRespMutex = threading.Lock()
# trackerRespMutex = threading.Lock()
trackerReqMutex = threading.Lock()
# piecesCollection = { pieceIndex : { blockIndex : data } } (make sure that requested blocks aren't overlapping data, please)
piecesCollection = {}
piecesCollectionMutex = threading.Lock()
# piecesStatus = { pieceIndex : pieceAmountHaveBytes }
piecesStatus = {}
piecesStatusMutex = threading.Lock()

workDeque = deque()
workDequeMutex = threading.Lock()

peer_obj_list = []