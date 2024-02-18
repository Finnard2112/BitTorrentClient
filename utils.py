import socket
import argparse
import bencodepy
import hashlib
import time
import datetime
import sys
import uuid
import urllib.parse
import math
import copy
import threading
import objects
import struct
import random
import selectors
import traceback
import random
from contextlib import redirect_stdout
from alive_progress import alive_bar
from bitstring import BitArray

UDP_MAGIC_NUM = 0x41727101980

# Option Parsing
def optParse():
    argParser = argparse.ArgumentParser(description="""BitTorrent is a protocol for distributing files. 
                                    It identifies content by URL and is designed to integrate seamlessly with the web. 
                                    Its advantage over plain HTTP is that when multiple downloads of the same file happen concurrently, the downloaders upload to each other, 
                                    making it possible for the file source to support very large numbers of downloaders with only a modest increase in its load.""")
    argParser.add_argument("-t", "--torrent", required=True, help="Specied .torrent file to parse")
    argParser.add_argument("-p", "--port", required=True, type=int, choices=range(1024, 49152), metavar="[1024, 49151]", help="Specied port which your client is listening on; BitTorrent ports are typically [6881, 6889] but not mandatory")
    argParser.add_argument("-c", "--compact", required=False, action='store_true', help="(Optional) Indicates that the client accepts a compact response from the tracker")
    argParser.add_argument("-n", "--noPeerId", required=False, action='store_true', help="(Optional [not supported by class tracker]) Indicates that the tracker can omit peer id field in peers dictionary. This option is ignored if compact is enabled.")
    argParser.add_argument("-w", "--numWant", required=False, type=int, default=50, help="(Optional) Number of peers that the client would like to receive from the tracker. This value is permitted to be zero. If omitted, defaults to 50 peers.")
    argParser.add_argument("-u", "--udp", required=False, action='store_true', help="(Optional) User may manually opt for support using a UDP-tracker protocol.")
    argParser.add_argument("-d", "--details", required=False, action='store_true', help="(Optional) Outputs real-time log of client behavior into console for details.")
    argParser.add_argument("-q", "--quit", required=False, action='store_true', help="(Optional) Informs the client to automatically disconnect from the tracker once complete download has finished; otherwise, it will remain part of the swarm as a seeder for peer downloads.")

    args = argParser.parse_args()

    if args.details:
        objects.DEBUG_MODE = True
        # currentTime = datetime.datetime.now()
        # currentTime = currentTime.replace(" ", "_")
        # currentTime = currentTime.replace(":", "-")
        # index = currentTime.find(".")
        # currentTime = currentTime[:index]
        # log = open('client-log-' + currentTime + '.txt', 'w')
        # redirect_stdout(log)
    else:
        objects.DEBUG_MODE = False

    if objects.DEBUG_MODE:
        print("\nOptions:\n\t- %s\n" % args)
        print("------------------------------------------------------------------")

    return args

# Create socket based on UDP or TCP (HTTP)
def establishSocket(udp, trackerInformation):
    if udp:
        if objects.DEBUG_MODE:
            print('INITIALIZED UDP SOCKET: Make sure to include %s:%d in each sendto() for tracker\n' % (trackerInformation.ip, trackerInformation.port))
            print("------------------------------------------------------------------")

        return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    else:
        trackerSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        trackerSocket.connect((trackerInformation.ip, trackerInformation.port))

        if objects.DEBUG_MODE:
            print('SOCKET CONNECTED: %s:%d\n' % (trackerInformation.ip, trackerInformation.port))
            print("------------------------------------------------------------------")

        return trackerSocket

def sendUdpConnect(trackerSocket, trackerInformation):
    transactionId = random.randint(0, 2**32 - 1)
    objects.trackerResponseMsg.transactionId = transactionId
    masterRequest = struct.pack('>QII', UDP_MAGIC_NUM, 0, transactionId)

    if objects.DEBUG_MODE:
        print('Local Transaction ID - %d' % transactionId)
        print('UDP Full Tracker Request:\n\t-', masterRequest)
    
    trackerSocket.sendto(masterRequest, (trackerInformation.ip, trackerInformation.port))
    # Going to assume blocking only on single thread, so main thread still falls through
    selector = selectors.DefaultSelector()
    selector.register(trackerSocket, selectors.EVENT_READ)
    x = 0

    while True:
        events = selector.select(timeout=(15*(2**x)))

        # Timeout hit
        if not events:
            if objects.DEBUG_MODE:
                print("Timeout reached. ReX UDP 'connect' msg.")
                trackerSocket.sendto(masterRequest, (trackerInformation.ip, trackerInformation.port))
            if x == 8:
                print("Client failed to connect to tracker via UDP after 8 attempts.  Quitting.")
                sys.exit()
            x += 1
        else:
            # Data in socket
            for key, mask in events:
                trackerResponse, addr = trackerSocket.recvfrom(4096)
                if objects.DEBUG_MODE:
                    print("Received connect UDP response")
            break

    selector.unregister(trackerSocket)
    selector.close()

    # Should prbly do check if recved from correct src here
    return trackerResponse

# Sends request data to tracker (UDP vs TCP) and receives response
def initConnect(udp, trackerSocket, trackerInformation):
    if udp:
        return sendUdpConnect(trackerSocket, trackerInformation)
    else:
        urlEncodedIH = urllib.parse.quote(objects.trackerRequestMsg.infoHash)
        masterRequest = (b"GET /announce?info_hash=" + urlEncodedIH.encode(trackerInformation.encoding)
                + b"&peer_id=" + objects.trackerRequestMsg.peerId.encode(trackerInformation.encoding)
                + b"&port=" + str(objects.trackerRequestMsg.port).encode(trackerInformation.encoding)
                + b"&uploaded=" + str(objects.trackerRequestMsg.uploaded).encode(trackerInformation.encoding)
                + b"&downloaded=" + str(objects.trackerRequestMsg.downloaded).encode(trackerInformation.encoding)
                + b"&left=" + str(objects.trackerRequestMsg.left).encode(trackerInformation.encoding)
                + b"&numwant=" + str(objects.trackerRequestMsg.numwant).encode(trackerInformation.encoding)
                + b"&compact=" + str(objects.trackerRequestMsg.compact).encode(trackerInformation.encoding)
                + b"&no_peer_id=" + str(objects.trackerRequestMsg.noPeerId).encode(trackerInformation.encoding)
                + b"&event=" + objects.trackerRequestMsg.event.encode(trackerInformation.encoding)
                + b" HTTP/1.1\r\n"
                + b"Host: " + trackerInformation.ip.encode(trackerInformation.encoding) + b":" + str(trackerInformation.port).encode(trackerInformation.encoding) + b"\r\n"
                + b"Accept: */*\r\n"
                + b"Accept-Encoding: deflate, gzip\r\n\r\n")
        if objects.DEBUG_MODE:
            print("masterRequest:\n\t-", masterRequest)
            print('')

        trackerSocket.send(masterRequest)
        trackerResponse = trackerSocket.recv(4096)
        return trackerResponse

# Debugging/Console State
def printMetaData(metaData):
    if objects.DEBUG_MODE:
        print('Metadata Information:')
        print('ENCODED:')
        print('\t-', metaData)
        decoded = bencodepy.decode(metaData)
        print('\nDECODED:')
        for each in decoded:
            print('\t- ', each, ":", decoded[each])
        print('')
        print("------------------------------------------------------------------")

# Get tracker IP & port
def getTrackerInfo(metaData, trackerInformation):
    decoded = bencodepy.decode(metaData)
    trackerIP = decoded[b'announce'].decode('utf-8')
    trackerPort = 80
    if "http://" in trackerIP:
        trackerIP = trackerIP.replace("http://", "")
    if ":" in trackerIP:
        afterPortIndex = trackerIP.find("/")
        index = trackerIP.find(":")
        trackerPort = int(trackerIP[(index + 1):afterPortIndex])
        trackerIP = trackerIP[:index]
    if b'encoding' not in decoded:
        encoding = 'utf-8'
    else:
        encoding = decoded[b'encoding'].decode('utf-8')
    pieces = decoded[b'info'][b'pieces']
    pieceHashList = []
    for piece in range(0, len(pieces), 20):
        pieceHashList.append(pieces[piece:(piece + 20)])
    
    # for each in pieceHashList:
    #     print("piece hash -", each, ", length - %d" % len(each))
    
    trackerInformation.ip = trackerIP
    trackerInformation.port = trackerPort
    trackerInformation.encoding = encoding
    trackerInformation.name = decoded[b'info'][b'name'].decode('utf-8')
    trackerInformation.length = decoded[b'info'][b'length']
    trackerInformation.pieceLength = decoded[b'info'][b'piece length']
    trackerInformation.pieces = pieceHashList
    if b'private' not in decoded[b'info']:
        trackerInformation.private = 0
    else:
        trackerInformation.private = decoded[b'info'][b'private']


    if objects.DEBUG_MODE:
        print("Tracker Information:")
        print("pieces length: %d" % len(decoded[b'info'][b'pieces']))
        print("\t- trackerIP = %s\n\t- trackerPort = %d\n\t- trackerEncoding = %s\n\t- trackerName = %s\n\t- private = %d\n\t- length = %d\n\t- pieceLength = %d\n\t- pieces =" % (trackerInformation.ip, trackerInformation.port, trackerInformation.encoding, trackerInformation.name, trackerInformation.private, trackerInformation.length, trackerInformation.pieceLength), trackerInformation.pieces)
        print("------------------------------------------------------------------")

# Extracting appropriate data from torrent file for appropriate tracker GET request
def parseTorr(metaData, args, trackerRequestMsg):
    decoded = bencodepy.decode(metaData)
    
    # Getting infoHash
    if b'info' in metaData:
        # index = metaData.index(b'info')
        infoHash = bencodepy.encode(decoded[b'info'])
        infoHash = hashlib.sha1(infoHash).digest()
    else:
        print('Err: Could not locate info key in bencoded torrent')
    
    # Getting peerId (uuid4 generates randomly generated unique id's)
    peerId = str(uuid.uuid4())
    peerId = peerId.replace("-", "")
    peerId = peerId[:20]
    if args.udp:
        peerId = urllib.parse.quote(peerId)

    # Getting port
    port = args.port

    # Getting uploaded & downloaded byte amounts (Initial request: No data sent/received)
    uploaded = 0
    downloaded = 0

    # Getting bytes left to download (Initial request: All data left to download)
    left = decoded[b'info'][b'length']

    # Getting number of peers that the client would like to receive from the tracker
    numwant = args.numWant

    # Getting compact request
    compact = 0
    if args.compact:
        compact = 1

    # Getting noPeerId request (omits peerId from dictionary list in TrackerResponse) 
    noPeerId = 0
    if args.noPeerId:
        noPeerId = 1

    # Getting event (The first request to the tracker must include the event key with 'started'.)
    event = "started"

    # Assigning all fields
    trackerRequestMsg.infoHash = infoHash
    trackerRequestMsg.peerId = peerId
    trackerRequestMsg.port = port
    trackerRequestMsg.uploaded = uploaded
    trackerRequestMsg.downloaded = downloaded
    trackerRequestMsg.left = left
    trackerRequestMsg.numwant = numwant
    trackerRequestMsg.compact = compact
    trackerRequestMsg.noPeerId = noPeerId
    trackerRequestMsg.event = event
    trackerRequestMsg.key = random.randint(0, 2**32 - 1)
    
    # Console printing for debugging
    if objects.DEBUG_MODE:
        print("Tracker Request Message:")
        print("\t- infoHash = ", end='')
        print(trackerRequestMsg.infoHash, '(length = %d bytes)' % len(trackerRequestMsg.infoHash))
        print("\t- peerId = %s (length = %d bytes)" % (trackerRequestMsg.peerId, len(trackerRequestMsg.peerId)))
        print("\t- port = %d" % trackerRequestMsg.port)
        print("\t- uploaded = %d\n\t- downloaded = %d" % (trackerRequestMsg.uploaded, trackerRequestMsg.downloaded))
        print("\t- left = %d" % trackerRequestMsg.left)
        print("\t- numwant = %d" % trackerRequestMsg.numwant)
        print("\t- compact = %d" % trackerRequestMsg.compact)
        print("\t- noPeerId = %d" % trackerRequestMsg.noPeerId)
        print("\t- event = %s" % trackerRequestMsg.event)
        print("\n------------------------------------------------------------------")

# Deprecated
def convert(i):
    bytes = math.ceil(i.bit_length() / 8) # minimum byte requirement
    return bytes

# First the UDP msg announcement
def firstUdpAnnounce(trackerSocket, trackerInformation):
    objects.trackerResponseMsg.transactionId = random.randint(0, 2**32 - 1)
    masterRequest = struct.pack('>QII20s20sQQQIIIIH', 
                                objects.trackerResponseMsg.connectionId[0], 
                                1,                                       # Action: Announce
                                objects.trackerResponseMsg.transactionId,
                                objects.trackerRequestMsg.infoHash,
                                objects.trackerRequestMsg.peerId.encode(trackerInformation.encoding),
                                objects.trackerRequestMsg.downloaded,
                                objects.trackerRequestMsg.left,
                                objects.trackerRequestMsg.uploaded,
                                2,                                       # Event: started
                                0,                                       # IP: default
                                objects.trackerRequestMsg.key,
                                objects.trackerRequestMsg.numwant,
                                objects.trackerRequestMsg.port)
    if objects.DEBUG_MODE:
        print("UDP (initial) Announce Tracker Request:\n")
        print("masterRequest:\n\t-", masterRequest)
        print('')
    
    trackerSocket.sendto(masterRequest, (trackerInformation.ip, trackerInformation.port))
    objects.trackerResponseMsg.numAnnounces += 1

    # Timeout & ReX functionality
    selector = selectors.DefaultSelector()
    selector.register(trackerSocket, selectors.EVENT_READ)
    x = 0
    
    while True:
        events = selector.select(timeout=(15*(2**x)))

        # Timeout hit
        if not events:
            if objects.DEBUG_MODE:
                print("Timeout reached. ReX UDP 'announce' msg.")
                trackerSocket.sendto(masterRequest, (trackerInformation.ip, trackerInformation.port))
                objects.trackerResponseMsg.numAnnounces += 1
            if objects.trackerResponseMsg.numAnnounces == 3:
                print("Client failed to get announce response from tracker via UDP after 3 attempts.  Must re'connect'.")
                objects.trackerResponseMsg.numAnnounces = 0
                sendUdpConnect(trackerSocket, trackerInformation)
                break
            x += 1
        else:
            # Data in socket
            for key, mask in events:
                trackerResponse, addr = trackerSocket.recvfrom(4096)
                if objects.DEBUG_MODE:
                    print("Received announce UDP response")
                if objects.trackerResponseMsg.numAnnounces == 3:
                    print("Client hit announce response limit from tracker via UDP at 3 attempts.  Must re'connect'.")
                    objects.trackerResponseMsg.numAnnounces = 0
                    sendUdpConnect(trackerSocket, trackerInformation)
            break

    selector.unregister(trackerSocket)
    selector.close()

    return trackerResponse

# Sort tracker bencoded data 
def parseTrackerResp(trackerInformation, trackerSocket, trackerResponse, args, trackerResponseMsg, udpAction='connect'):
    
    if args.udp:
        if objects.DEBUG_MODE:
            print('UDP Full Tracker Response:\n\t-', trackerResponse, '\n')

        if udpAction == 'connect':
            if len(trackerResponse) >= 16:
                data = struct.unpack('>IIQ', trackerResponse)
                print("Unpacked UDP Response Fields:\n\t- action - %d\n\t- transactionId - %d\n\t- connectionId - %d\n" % (data[0], data[1], data[2]))
                # print("objects.trackerResponseMsg.transactionId = %d" % objects.trackerResponseMsg.transactionId)
                if data[0] == 0 and data[1] == objects.trackerResponseMsg.transactionId:
                    # Store connection_id for later use
                    objects.trackerResponseMsg.connectionId.clear()
                    objects.trackerResponseMsg.connectionId.append(data[2])

                    if objects.DEBUG_MODE:
                        print("UDP Successfully Connected to Tracker!")
                        # print("UDP Fields:\n\t- action - %d\n\t- transactionId - %d\n\t- connectionId - %d\n" % (objects.trackerResponseMsg.action, objects.trackerResponseMsg.transactionId, objects.trackerResponseMsg.connectionId[0]))
                else:
                    print("UDP Tracker Response Err (connect): action != 'connect' or transaction_id != local 'transactionId'")
            else:
                print("UDP Tracker Response Err (connect): size < 16 bytes!")
        elif udpAction == 'announce':
            if len(trackerResponse) >= 20:
                data = struct.unpack('>IIIII', trackerResponse[:20])
                if data[0] == 1 and data[1] == objects.trackerResponseMsg.transactionId:
                    objects.trackerResponseMsg.interval = data[2]
                    objects.trackerResponseMsg.incomplete = data[3]
                    objects.trackerResponseMsg.complete = data[4]
                    totalPeers = objects.trackerResponseMsg.incomplete + objects.trackerResponseMsg.complete
                    for peer in range(totalPeers):
                        # data = struct.unpack('>IH', trackerResponse[(20 + (peer * 6)):(20 + ((peer + 1) * 6))])
                        data = trackerResponse[(20 + (peer * 6)):(20 + ((peer + 1) * 6))]

                        # Chunks of 6
                        peerIP = str(data[0]) + '.' + str(data[1]) + '.' + str(data[2]) + '.' + str(data[3])
                        peerPort = int.from_bytes(data[4:], byteorder='big')
                        trackerResponseMsg.peers.append({'ip':peerIP, 'port':peerPort})
                else:
                    print("UDP Tracker Response Err (announce): action != 'announce' or transaction_id != local 'transactionId'")
            else:
                print("UDP Tracker Response Err (announce): size < 20 bytes!")
        else: # Error response
            print("temporary")

    else:
        if objects.DEBUG_MODE:
            print('Full Tracker Response:\n\t-', trackerResponse, '\n')

        firstChunk = True
        # Handles response with transfer-encoding: chunked
        if b'Transfer-Encoding: chunked\r\n\r\n' in trackerResponse:

            # Looping if more chunks
            while b'0\r\n\r\n' not in trackerResponse:
                # Isolating response body
                bencodeStartIndex = trackerResponse.find(b"\r\n\r\n") + 4
                bencodeStartIndex = bencodeStartIndex + trackerResponse[bencodeStartIndex:].find(b"\r\n") + 2
                bencodeResp = trackerResponse[bencodeStartIndex:]
                bencodeEndIndex = bencodeResp.find(b"ee\r\n")
                bencodeResp = bencodeResp[:bencodeEndIndex + 2]
                printMetaData(bencodeResp)
                try:
                    decodedResp = bencodepy.decode(bencodeResp)
                except bencodepy.BencodeDecodeError:
                    print("Err: Encountered Mangled Tracker Response. Cannot decode. Quitting.")
                    sendStopped(trackerInformation, trackerSocket, args)
                    sys.exit()

                sortRespIntoObj(args, decodedResp, trackerResponseMsg, chunked=True, firstChunk=firstChunk)
                trackerResponse = trackerSocket.recv(4096)
                firstChunk=False
            
            # Encountered end-chunk; parse last response
            # Isolating response body
            bencodeStartIndex = trackerResponse.find(b"\r\n\r\n") + 4
            bencodeStartIndex = bencodeStartIndex + trackerResponse[bencodeStartIndex:].find(b"\r\n") + 2
            bencodeResp = trackerResponse[bencodeStartIndex:]
            bencodeEndIndex = bencodeResp.find(b"ee\r\n")
            bencodeResp = bencodeResp[:bencodeEndIndex + 2]
            printMetaData(bencodeResp)
            try:
                decodedResp = bencodepy.decode(bencodeResp)
            except bencodepy.BencodeDecodeError:
                print("Err: Encountered Mangled Tracker Response. Cannot decode. Quitting.")
                sendStopped(trackerInformation, trackerSocket, args)
                sys.exit()

            sortRespIntoObj(args, decodedResp, trackerResponseMsg, chunked=True, firstChunk=firstChunk)
        
        # Standard Response
        else:
            # Isolating response body
            bencodeStartIndex = trackerResponse.find(b"\r\n\r\nd")
            bencodeResp = trackerResponse[bencodeStartIndex + 4:]
            printMetaData(bencodeResp)
            try:
                decodedResp = bencodepy.decode(bencodeResp)
            except bencodepy.BencodeDecodeError:
                print("Err: Encountered Mangled Tracker Response. Cannot decode. Quitting.")
                sendStopped(trackerInformation, trackerSocket, args)
                sys.exit()

            # Covering case where tracker responds w/ compact even when not specified
            try:
                print("Tracker sent compact response even when we didn't want it to :(. Adjusting config... ", decodedResp[b'peers'][0][b'ip'])
            except TypeError:
                print("Tracker sent compact response even when we didn't want it to :(. Adjusting config... ")
                args.compact = True
                objects.trackerRequestMsg.compact = True
            sortRespIntoObj(args, decodedResp, trackerResponseMsg)

# Sorting parsed tracker response data into trackerResponseMsg
def sortRespIntoObj(args, decodedResp, trackerResponseMsg, chunked=False, firstChunk=False):
    # COMPACT:
    # (binary model) Instead of using the dictionary model described above, the peers value may be a string consisting of multiples of 6 bytes.
    # First 4 bytes are the IP address and last 2 bytes are the port number.
    # All in network (big endian) notation.
    if args.compact:
        if b'failure reason' in decodedResp:
            # If this is present, everything else empty
            trackerResponseMsg.failureReason = decodedResp[b'failure reason']
            print("Tracker Response Error: %s" % trackerResponseMsg.failureReason)

            # Reset
            trackerResponseMsg.failureReason = None
        else:
            # The following are optional fields
            if b'warning message' in decodedResp:
                trackerResponseMsg.warningMsg = decodedResp[b'warning message']
                print("Tracker Warning Message: %s" % trackerResponseMsg.warningMsg)
            if b'min interval' in decodedResp:
                trackerResponseMsg.minInterval = decodedResp[b'min interval']
            if b'tracker id' in decodedResp:
                trackerResponseMsg.trackerId = decodedResp[b'tracker id']

            # Mandatory fields
            
            trackerResponseMsg.interval = decodedResp[b'interval']
            if b'complete' not in decodedResp or b'incomplete' not in decodedResp:
                trackerResponseMsg.complete = 0
                trackerResponseMsg.incomplete = len(decodedResp[b'peers'])
            else:
                trackerResponseMsg.complete = decodedResp[b'complete']
                trackerResponseMsg.incomplete = decodedResp[b'incomplete']
            # We aggregate peers if received chunked response, else replace
            if (not chunked) or firstChunk:
                trackerResponseMsg.peers.clear()

            # Chunks of 6
            peersList = decodedResp[b'peers']
            for byte in range(0, len(peersList), 6):
                peerIP = str(peersList[byte]) + '.' + str(peersList[byte + 1]) + '.' + str(peersList[byte + 2]) + '.' + str(peersList[byte + 3])
                peerPort = int.from_bytes(peersList[byte + 4 : byte + 6], byteorder='big')
                trackerResponseMsg.peers.append({'ip':peerIP, 'port':peerPort})

    # NO_PEER_ID: (T.A. SAID NOT TO WORRY ABT THIS no_peer_id FEATURE lol)
    # Indicates that the tracker can omit peer id field in peers dictionary.
    # This option is ignored if compact is enabled.
    elif args.noPeerId:
        if b'failure reason' in decodedResp:
            # If this is present, everything else empty
            trackerResponseMsg.failureReason = decodedResp[b'failure reason']
        else:
            # The following are optional fields
            if b'warning message' in decodedResp:
                trackerResponseMsg.warningMsg = decodedResp[b'warning message']
            if b'min interval' in decodedResp:
                trackerResponseMsg.minInterval = decodedResp[b'min interval']
            if b'tracker id' in decodedResp:
                trackerResponseMsg.trackerId = decodedResp[b'tracker id']

            # Mandatory fields
            trackerResponseMsg.interval = decodedResp[b'interval']
            trackerResponseMsg.complete = decodedResp[b'complete']
            trackerResponseMsg.incomplete = decodedResp[b'incomplete']
            # We aggregate peers if received chunked response, else replace
            if chunked:
                if firstChunk:
                    trackerResponseMsg.peers.clear()

                peersCpy = copy.deepcopy(decodedResp[b'peers'])
                for peer in peersCpy:
                    peer['ip'] = peer.pop(b'ip')
                    peer['port'] = peer.pop(b'port')
                    peer['peer id'] = peer.pop(b'peer id')
                trackerResponseMsg.peers += peersCpy
            else:
                trackerResponseMsg.peers = copy.deepcopy(decodedResp[b'peers'])
                for peer in trackerResponseMsg.peers:
                    peer['ip'] = peer.pop(b'ip')
                    peer['port'] = peer.pop(b'port')
                    peer['peer id'] = peer.pop(b'peer id')

    # STANDARD:
    # (dictionary model) The value is a list of dictionaries, each with the following keys:
    #   - peer id : peer's self-selected ID, as described above for the tracker request (string)
    #   -      ip : peer's IP address either IPv6 (hexed) or IPv4 (dotted quad) or DNS name (string)
    #   -    port : peer's port number (integer)
    else:
        if b'failure reason' in decodedResp:
            # If this is present, everything else empty
            trackerResponseMsg.failureReason = decodedResp[b'failure reason'] # What to do when received this?? - future implementation
        else:
            # The following are optional fields
            if b'warning message' in decodedResp:
                trackerResponseMsg.warningMsg = decodedResp[b'warning message']
            if b'min interval' in decodedResp:
                trackerResponseMsg.minInterval = decodedResp[b'min interval']
            if b'tracker id' in decodedResp:
                trackerResponseMsg.trackerId = decodedResp[b'tracker id']
            if b'complete' not in decodedResp or b'incomplete' not in decodedResp:
                trackerResponseMsg.complete = 0
                trackerResponseMsg.incomplete = len(decodedResp[b'peers'])
            else:
                trackerResponseMsg.complete = decodedResp[b'complete']
                trackerResponseMsg.incomplete = decodedResp[b'incomplete']

            # Mandatory fields
            trackerResponseMsg.interval = decodedResp[b'interval']
            
            # We aggregate peers if received chunked response, else replace
            if chunked:
                if firstChunk:
                    trackerResponseMsg.peers.clear()

                peersCpy = copy.deepcopy(decodedResp[b'peers'])
                for peer in peersCpy:
                    peer['ip'] = peer.pop(b'ip')
                    peer['port'] = peer.pop(b'port')
                    peer['peer id'] = peer.pop(b'peer id')
                trackerResponseMsg.peers += peersCpy
            else:
                trackerResponseMsg.peers = copy.deepcopy(decodedResp[b'peers'])
                for peer in trackerResponseMsg.peers:
                    peer['ip'] = peer.pop(b'ip')
                    peer['port'] = peer.pop(b'port')
                    peer['peer id'] = peer.pop(b'peer id')

# Not worrying about this
def parseScrapeResp(scrapeResponse):
    if objects.DEBUG_MODE:
        print('Full Scrape Response:\n\t-', scrapeResponse, '\n')

    # Isolating response body
    bencodeStartIndex = scrapeResponse.find(b"\r\n\r\nd")
    bencodeResp = scrapeResponse[bencodeStartIndex + 4:]
    printMetaData(bencodeResp)
    try:
        decodedResp = bencodepy.decode(bencodeResp)
    except bencodepy.BencodeDecodeError:
        print("Err: Encountered Mangled Tracker Response. Cannot decode. Quitting.")
        # sendStopped(trackerInformation, trackerSocket, args)
        sys.exit()

    # Assigned values
    file = decodedResp[objects.trackerRequestMsg.infoHash]
    objects.trackerResponseMsg.complete = file[b'complete']
    objects.trackerResponseMsg.incomplete = file[b'incomplete']
    objects.trackerResponseMsg.downloaded = file[b'downloaded']

def trackerTimer(name, count):
    for i in range(count):
        time.sleep(1)
        if objects.DEBUG_MODE and i % 4 == 0:
            print("\t- %s Pdc clock @%dsecs - @%dsecs ReX\n" % (name, i, count))

def pdcTrackerAnnounce(trackerInformation, trackerSocket, args):
    while True:
        trackerTimer("Announce", objects.trackerResponseMsg.interval)
        if args.udp:
            masterRequest = struct.pack('>QII20s20sQQQIIIIH', 
                                objects.trackerResponseMsg.connectionId[0], 
                                1,                                       # Action: Announce
                                objects.trackerResponseMsg.transactionId,
                                objects.trackerRequestMsg.infoHash,
                                objects.trackerRequestMsg.peerId,
                                objects.trackerRequestMsg.downloaded,
                                objects.trackerRequestMsg.left,
                                objects.trackerRequestMsg.uploaded,
                                0,                                       # Event: none
                                0,                                       # IP: default
                                objects.trackerRequestMsg.key,
                                objects.trackerRequestMsg.numwant,
                                objects.trackerRequestMsg.port)
            if objects.DEBUG_MODE:
                print("UDP Announce Tracker Request:\n")
                print("masterRequest:\n\t-", masterRequest)
                print('')

            trackerSocket.sendto(masterRequest, (trackerInformation.ip, trackerInformation.port))
            objects.trackerResponseMsg.numAnnounces += 1

            # Timeout & ReX functionality
            selector = selectors.DefaultSelector()
            selector.register(trackerSocket, selectors.EVENT_READ)
            x = 0
            
            while True:
                events = selector.select(timeout=(15*(2**x)))

                # Timeout hit
                if not events:
                    if objects.DEBUG_MODE:
                        print("Timeout reached. ReX UDP 'announce' msg.")
                        trackerSocket.sendto(masterRequest, (trackerInformation.ip, trackerInformation.port))
                        objects.trackerResponseMsg.numAnnounces += 1
                    if objects.trackerResponseMsg.numAnnounces == 3:
                        print("Client failed to get announce response from tracker via UDP after 3 attempts.  Must re'connect'.")
                        objects.trackerResponseMsg.numAnnounces = 0
                        sendUdpConnect(trackerSocket, trackerInformation)
                        break
                    x += 1
                else:
                    # Data in socket
                    for key, mask in events:
                        trackerResponse, addr = trackerSocket.recvfrom(4096)
                        if objects.DEBUG_MODE:
                            print("Received announce UDP response")
                        if objects.trackerResponseMsg.numAnnounces == 3:
                            print("Client hit announce response limit from tracker via UDP at 3 attempts.  Must re'connect'.")
                            objects.trackerResponseMsg.numAnnounces = 0
                            sendUdpConnect(trackerSocket, trackerInformation)
                    break

            selector.unregister(trackerSocket)
            selector.close()

            # Received non-empty tracker response
            if not trackerResponse == b'':
                objects.trackerRespMutex.acquire()
                parseTrackerResp(trackerInformation, trackerSocket, trackerResponse, args, objects.trackerResponseMsg, udpAction='announce')
                objects.trackerResponseMsg.printState()
                objects.trackerRespMutex.release()
            else:
                if objects.DEBUG_MODE:
                    print('Received empty periodic tracker response (UDP)')
        else:
            urlEncodedIH = urllib.parse.quote(objects.trackerRequestMsg.infoHash)
            masterRequest = (b"GET /announce?info_hash=" + urlEncodedIH.encode(trackerInformation.encoding)
                            + b"&peer_id=" + objects.trackerRequestMsg.peerId.encode(trackerInformation.encoding)
                            + b"&port=" + str(objects.trackerRequestMsg.port).encode(trackerInformation.encoding)
                            + b"&uploaded=" + str(objects.trackerRequestMsg.uploaded).encode(trackerInformation.encoding)       # Uploaded amount should be changing
                            + b"&downloaded=" + str(objects.trackerRequestMsg.downloaded).encode(trackerInformation.encoding)   # Downloaded amount should be changing
                            + b"&left=" + str(objects.trackerRequestMsg.left).encode(trackerInformation.encoding)               # Data left amount should be changing
                            + b"&numwant=" + str(objects.trackerRequestMsg.numwant).encode(trackerInformation.encoding)
                            + b"&compact=" + str(objects.trackerRequestMsg.compact).encode(trackerInformation.encoding)
                            + b"&no_peer_id=" + str(objects.trackerRequestMsg.noPeerId).encode(trackerInformation.encoding)
                            + b" HTTP/1.1\r\n"
                            + b"Host: " + trackerInformation.ip.encode(trackerInformation.encoding) + b":" + str(trackerInformation.port).encode(trackerInformation.encoding) + b"\r\n"
                            + b"Accept: */*\r\n"
                            + b"Accept-Encoding: deflate, gzip\r\n\r\n")
                            # Notice no 'event' parameter set
            if objects.DEBUG_MODE:
                print("Periodic Announce Tracker Request:\n")
                print("masterRequest:\n\t-", masterRequest)
                print('')
            trackerSocket.send(masterRequest)
            trackerResponse = trackerSocket.recv(4096)

            # Received non-empty tracker response
            if not trackerResponse == b'':
                objects.trackerRespMutex.acquire()
                parseTrackerResp(trackerInformation, trackerSocket, trackerResponse, args, objects.trackerResponseMsg)
                objects.trackerResponseMsg.printState()
                objects.trackerRespMutex.release()
            else:
                if objects.DEBUG_MODE:
                    print('Received empty periodic tracker response')
            
        print("\n------------------------------------------------------------------")

def pdcTrackerScrape(trackerSocket, trackerInformation):
    while True:
        trackerTimer("Scrape", 30)
        masterRequest = (b"GET /scrape?info_hash=" + objects.trackerRequestMsg.infoHash
                        + b" HTTP/1.1\r\n"
                        + b"Host: " + trackerInformation.ip.encode(trackerInformation.encoding) + b":" + str(trackerInformation.port).encode(trackerInformation.encoding) + b"\r\n"
                        + b"Accept: */*\r\n"
                        + b"Accept-Encoding: deflate, gzip\r\n\r\n")
        if objects.DEBUG_MODE:
            print("Periodic Scrape Tracker Request:\n")
            print("masterRequest:\n\t-", masterRequest)
            print('')
        trackerSocket.send(masterRequest)
        scrapeResponse = trackerSocket.recv(4096)

        # Received non-empty tracker response
        if not scrapeResponse == b'':
            objects.trackerRespMutex.acquire()
            parseScrapeResp(scrapeResponse)
            objects.trackerResponseMsg.printState()
            objects.trackerRespMutex.release()
        else:
            if objects.DEBUG_MODE:
                print('Received empty periodic tracker response')

        print("\n------------------------------------------------------------------")

def pdcProgressBar(totalBytes, origStdout, trackerInformation, trackerSocket, args):
    with alive_bar(totalBytes, file=origStdout, title="Downloading " + trackerInformation.name + " (bytes):", manual=True) as bar:
        while objects.trackerRequestMsg.downloaded <= totalBytes:
            time.sleep(0.5)
            bar(objects.trackerRequestMsg.downloaded / totalBytes)

        # Sending event=completed request to tracker
        if args.udp:
            objects.trackerRequestMsg.event = "completed"
            masterRequest = struct.pack('>QII20s20sQQQIIIIH', 
                                objects.trackerResponseMsg.connectionId[0], 
                                1,                                       # Action: Announce
                                objects.trackerResponseMsg.transactionId,
                                objects.trackerRequestMsg.infoHash,
                                objects.trackerRequestMsg.peerId,
                                objects.trackerRequestMsg.downloaded,
                                objects.trackerRequestMsg.left,
                                objects.trackerRequestMsg.uploaded,
                                1,                                       # Event: completed
                                0,                                       # IP: default
                                objects.trackerRequestMsg.key,
                                objects.trackerRequestMsg.numwant,
                                objects.trackerRequestMsg.port)
            if objects.DEBUG_MODE:
                print('Sending \'downloaded\' msg to tracker:')
                print("masterRequest:\n\t-", masterRequest)
                print('')
            
            trackerSocket.sendto(masterRequest, (trackerInformation.ip, trackerInformation.port))
            objects.trackerResponseMsg.numAnnounces += 1

            # Timeout & ReX functionality
            selector = selectors.DefaultSelector()
            selector.register(trackerSocket, selectors.EVENT_READ)
            x = 0
            
            while True:
                events = selector.select(timeout=(15*(2**x)))

                # Timeout hit
                if not events:
                    if objects.DEBUG_MODE:
                        print("Timeout reached. ReX UDP 'announce' msg.")
                        trackerSocket.sendto(masterRequest, (trackerInformation.ip, trackerInformation.port))
                        objects.trackerResponseMsg.numAnnounces += 1
                    if objects.trackerResponseMsg.numAnnounces == 3:
                        print("Client failed to get announce response from tracker via UDP after 3 attempts.  Must re'connect'.")
                        objects.trackerResponseMsg.numAnnounces = 0
                        sendUdpConnect(trackerSocket, trackerInformation)
                        break
                    x += 1
                else:
                    # Data in socket
                    for key, mask in events:
                        trackerResponse, addr = trackerSocket.recvfrom(4096)
                        if objects.DEBUG_MODE:
                            print("Received announce UDP response")

                        # If '-q' option was toggled, automatically exit from swarm
                        if args.quit:
                            sendStopped(trackerInformation, trackerSocket, args)
                            sys.exit()
                        else:
                            if objects.DEBUG_MODE:
                                print("Our client is now seeder!")
                            # Potential Feature implementation: Print total bytes uploading to peers

                        if objects.trackerResponseMsg.numAnnounces == 3:
                            print("Client hit announce response limit from tracker via UDP at 3 attempts.  Must re'connect'.")
                            objects.trackerResponseMsg.numAnnounces = 0
                            sendUdpConnect(trackerSocket, trackerInformation)
                    break

            selector.unregister(trackerSocket)
            selector.close()

            if not trackerResponse == b'':
                parseTrackerResp(trackerInformation, trackerSocket, trackerResponse, args, objects.trackerResponseMsg)
            else:
                if objects.DEBUG_MODE:
                    print('Received empty periodic tracker response')
                    print("\n------------------------------------------------------------------")
        else:
            objects.trackerRequestMsg.event = "completed"
            urlEncodedIH = urllib.parse.quote(objects.trackerRequestMsg.infoHash)
            masterRequest = (b"GET /announce?info_hash=" + urlEncodedIH.encode(trackerInformation.encoding)
                    + b"&peer_id=" + objects.trackerRequestMsg.peerId.encode(trackerInformation.encoding)
                    + b"&port=" + str(objects.trackerRequestMsg.port).encode(trackerInformation.encoding)
                    + b"&uploaded=" + str(objects.trackerRequestMsg.uploaded).encode(trackerInformation.encoding)
                    + b"&downloaded=" + str(objects.trackerRequestMsg.downloaded).encode(trackerInformation.encoding)
                    + b"&left=" + str(objects.trackerRequestMsg.left).encode(trackerInformation.encoding)
                    + b"&numwant=" + str(objects.trackerRequestMsg.numwant).encode(trackerInformation.encoding)
                    + b"&compact=" + str(objects.trackerRequestMsg.compact).encode(trackerInformation.encoding)
                    + b"&no_peer_id=" + str(objects.trackerRequestMsg.noPeerId).encode(trackerInformation.encoding)
                    + b"&event=" + objects.trackerRequestMsg.event.encode(trackerInformation.encoding)
                    + b" HTTP/1.1\r\n"
                    + b"Host: " + trackerInformation.ip.encode(trackerInformation.encoding) + b":" + str(trackerInformation.port).encode(trackerInformation.encoding) + b"\r\n"
                    + b"Accept: */*\r\n"
                    + b"Accept-Encoding: deflate, gzip\r\n\r\n")
            if objects.DEBUG_MODE:
                print('Sending \'downloaded\' msg to tracker:')
                print("masterRequest:\n\t-", masterRequest)
                print('')
            
            trackerSocket.send(masterRequest)
            trackerResponse = trackerSocket.recv(4096)

            if not trackerResponse == b'':
                parseTrackerResp(trackerInformation, trackerSocket, trackerResponse, args, objects.trackerResponseMsg)
            else:
                if objects.DEBUG_MODE:
                    print('Received empty periodic tracker response')
                    print("\n------------------------------------------------------------------")
        
        # If '-q' option was toggled, automatically exit from swarm
        if args.quit:
            sendStopped(trackerInformation, trackerSocket, args)
            sys.exit()
        else:
            if objects.DEBUG_MODE:
                print("Our client is now seeder!")
            # Potential Feature implementation: Print total bytes uploading to peers

def sendStopped(trackerInformation, trackerSocket, args):
    if args.udp:
        objects.trackerRequestMsg.event = "stopped"
        masterRequest = struct.pack('>QII20s20sQQQIIIIH', 
                            objects.trackerResponseMsg.connectionId[0], 
                            1,                                       # Action: Announce
                            objects.trackerResponseMsg.transactionId,
                            objects.trackerRequestMsg.infoHash,
                            objects.trackerRequestMsg.peerId,
                            objects.trackerRequestMsg.downloaded,
                            objects.trackerRequestMsg.left,
                            objects.trackerRequestMsg.uploaded,
                            1,                                       # Event: stopped
                            0,                                       # IP: default
                            objects.trackerRequestMsg.key,
                            objects.trackerRequestMsg.numwant,
                            objects.trackerRequestMsg.port)
        if objects.DEBUG_MODE:
            print('Sending \'stopped\' msg to tracker:')
            print("masterRequest:\n\t-", masterRequest)
            print('')
        
        trackerSocket.sendto(masterRequest, (trackerInformation.ip, trackerInformation.port))
        objects.trackerResponseMsg.numAnnounces += 1

        # Timeout & ReX functionality
        selector = selectors.DefaultSelector()
        selector.register(trackerSocket, selectors.EVENT_READ)
        x = 0
        
        while True:
            events = selector.select(timeout=(15*(2**x)))

            # Timeout hit
            if not events:
                if objects.DEBUG_MODE:
                    print("Timeout reached. ReX UDP 'announce' msg.")
                    trackerSocket.sendto(masterRequest, (trackerInformation.ip, trackerInformation.port))
                    objects.trackerResponseMsg.numAnnounces += 1
                if objects.trackerResponseMsg.numAnnounces == 3:
                    print("Client failed to get announce response from tracker via UDP after 3 attempts.  Bailing. (Ungraceful disconnect?)")
                    break
                x += 1
            else:
                # Data in socket
                for key, mask in events:
                    trackerResponse, addr = trackerSocket.recvfrom(4096)
                    if objects.DEBUG_MODE:
                        print("Received announce UDP response. Graceful Disconnect.")
                break

        selector.unregister(trackerSocket)
        selector.close()

        if not trackerResponse == b'':
            parseTrackerResp(trackerInformation, trackerSocket, trackerResponse, args, objects.trackerResponseMsg)
        else:
            if objects.DEBUG_MODE:
                print('Received empty periodic tracker response')
                print("\n------------------------------------------------------------------")
    else:
        urlEncodedIH = urllib.parse.quote(objects.trackerRequestMsg.infoHash)
        masterRequest = (b"GET /announce?info_hash=" + urlEncodedIH.encode(trackerInformation.encoding)
                        + b"&peer_id=" + objects.trackerRequestMsg.peerId.encode(trackerInformation.encoding)
                        + b"&port=" + str(objects.trackerRequestMsg.port).encode(trackerInformation.encoding)
                        + b"&uploaded=" + str(objects.trackerRequestMsg.uploaded).encode(trackerInformation.encoding)
                        + b"&downloaded=" + str(objects.trackerRequestMsg.downloaded).encode(trackerInformation.encoding)
                        + b"&left=" + str(objects.trackerRequestMsg.left).encode(trackerInformation.encoding)
                        + b"&numwant=" + str(objects.trackerRequestMsg.numwant).encode(trackerInformation.encoding)
                        + b"&compact=" + str(objects.trackerRequestMsg.compact).encode(trackerInformation.encoding)
                        + b"&no_peer_id=" + str(objects.trackerRequestMsg.noPeerId).encode(trackerInformation.encoding)
                        + b"&event=" + objects.trackerRequestMsg.event.encode(trackerInformation.encoding)
                        + b" HTTP/1.1\r\n"
                        + b"Host: " + trackerInformation.ip.encode(trackerInformation.encoding) + b":" + str(trackerInformation.port).encode(trackerInformation.encoding) + b"\r\n"
                        + b"Accept: */*\r\n"
                        + b"Accept-Encoding: deflate, gzip\r\n\r\n")
        if objects.DEBUG_MODE:
            print('Sending \'stopping\' msg to tracker:')
            print("masterRequest:\n\t-", masterRequest)
            print('')
        
        trackerSocket.send(masterRequest)
        trackerResponse = trackerSocket.recv(4096)

        if not trackerResponse == b'':
            parseTrackerResp(trackerInformation, trackerSocket, trackerResponse, args, objects.trackerResponseMsg)
        else:
            if objects.DEBUG_MODE:
                print('Received empty periodic tracker response')
                print("\n------------------------------------------------------------------")

def verifyHash(totalPiece, pieceIndex, trackerInformation):
    print("ATTEMPTING TO VERIFY HASH!!")
    pieceHash = hashlib.sha1(totalPiece).digest()

    print("Expected Hash -", trackerInformation.pieces[pieceIndex])
    print("Calculated Hash -", pieceHash)
    # Need to find which piece hash to compare to
    if pieceHash == trackerInformation.pieces[pieceIndex]:
        if objects.DEBUG_MODE:
            print("SHA-1 Hash is correct!")
        return True
    else:
        return False


def combineBlocks(pieceIndex):
    print("ATTEMPTING TO COMBINE BLOCKS")
    # Combining blocks from smallest-to-largest index
    totalPiece = b''
    for block in sorted(objects.piecesCollection[pieceIndex].keys()):
        # print("Block Index -", block, " Block data -", objects.piecesCollection[pieceIndex][block])
        totalPiece += objects.piecesCollection[pieceIndex][block]

    # print("Total Block -", totalPiece)

    return totalPiece

def writePieceToFile(pieceIndex, totalPiece, trackerInformation):
    with open(trackerInformation.name, 'a+b') as file:
        # Move cursor to correct position in file & write
        file.seek(pieceIndex*trackerInformation.pieceLength)
        file.write(totalPiece)
        if objects.DEBUG_MODE:
            print("Successfully wrote piece to file!")

# If gotten an entire piece, verify then write to file on disk at 'x'
def verifyWholePiece(pieceIndex, trackerInformation):
    
    totalPiece = combineBlocks(pieceIndex)
    matchingHash = verifyHash(totalPiece, pieceIndex, trackerInformation)
    if matchingHash:
        # Write to file in disk at index 'x'
        writePieceToFile(pieceIndex, totalPiece, trackerInformation)
        have_msg = objects.messages()
        have_msg.have(struct.pack('>I', pieceIndex))
        for peer in objects.peer_obj_list:
            if peer.isAlive:
                peer.send_message(have_msg)

        objects.piecesCollection[pieceIndex] = None
        objects.piecesStatus[pieceIndex] = None # Should implement behavior such that if 'None' is encountered, we already have correct full piece (deny any data from this piece)
    else:
        # Dump piece, and start from empty again
        # Changing global object data
        objects.trackerRequestMsg.downloaded -= objects.piecesStatus[pieceIndex]
        objects.trackerRequestMsg.left += objects.piecesStatus[pieceIndex]

        # Reset containers for piece
        # Finn's change: Im just gonna pop the index since setting it to 
        # {} and 0 is confusing as to whether we have data on a block/piece or not

        # objects.piecesCollection[pieceIndex] = {}
        # objects.piecesStatus[pieceIndex] = 0
        objects.piecesCollection.pop(pieceIndex)
        objects.piecesStatus.pop(pieceIndex)

        # Production: Notify User why progress bar decreased
        print("Incorrect Piece Data due to Non-Matching SHA1 Hash: Resetting Piece @%d (- %d bytes)" % (pieceIndex, len(totalPiece)))

# Parses peer's piece msg and adds block to respective piece in pieces dictionary
def addBlockToPiece(pieceIndex, blockIndex, blockData, trackerInformation, blockLen):
    
    # Note: the 'piecesCollection' storage data structure is complex, look at bottom of objects.py for details 
    if pieceIndex in objects.piecesCollection and pieceIndex in objects.piecesStatus:
        # Checking if recved block is already part of complete downloaded piece
        if objects.piecesStatus[pieceIndex] == None:
            if objects.DEBUG_MODE:
                print("Already have complete downloaded piece @%d index. Dumping..." % pieceIndex)
        else:
            # Checking if we already have recved block in piece 
            if (objects.piecesCollection[pieceIndex] is None) or blockIndex in objects.piecesCollection[pieceIndex]:
                if objects.DEBUG_MODE:
                    print("Already have block of data @%d index, piece index @%d. Dumping..." % (blockIndex, pieceIndex))
            else:
                objects.piecesCollection[pieceIndex][blockIndex] = blockData
                objects.piecesStatus[pieceIndex] += blockLen

                # Changing global object data
                objects.trackerRequestMsg.downloaded += blockLen
                objects.trackerRequestMsg.left -= blockLen
    else:
        objects.piecesCollection[pieceIndex] = {blockIndex:blockData}
        objects.piecesStatus[pieceIndex] = blockLen

        # Changing global object data
        objects.trackerRequestMsg.downloaded += blockLen
        objects.trackerRequestMsg.left -= blockLen

    # Commenting this out in case I'm wrong. But don't you only need to check if 
    # objects.piecesStatus[pieceIndex] == trackerInformation.pieceLength to verifyWholePiece

    # This is for edge case of last piece (may have smaller piece length than others)
    isLastPiece = False
    maxPieceIndex = max(objects.piecesCollection)  
    if trackerInformation.length - (maxPieceIndex * trackerInformation.pieceLength) < trackerInformation.pieceLength:
        isLastPiece = True
        lastPieceLen = trackerInformation.length - (maxPieceIndex * trackerInformation.pieceLength)

    # Checking if gotten entire piece yet
    if isLastPiece:
        print("piecesStatus total bytes downloaded -", objects.piecesStatus[pieceIndex], "vs last pieceLength -", lastPieceLen)
        if objects.piecesStatus[pieceIndex] == lastPieceLen:
            verifyWholePiece(pieceIndex, trackerInformation)
    else:
        print("piecesStatus total bytes downloaded -", objects.piecesStatus[pieceIndex], "vs trackerInformation.pieceLength -", trackerInformation.pieceLength)
        if objects.piecesStatus[pieceIndex] == trackerInformation.pieceLength:
            verifyWholePiece(pieceIndex, trackerInformation)

    # print("piecesStatus total bytes downloaded -", objects.piecesStatus[pieceIndex], "vs trackerInformation.pieceLength -", trackerInformation.pieceLength)
    # if objects.piecesStatus[pieceIndex] == trackerInformation.pieceLength:
    #     print("MATCHING TOTAL PIECE LENGTH, NOW VERIFYING HASH & COMBINING BLOCKS")
    #     verifyWholePiece(pieceIndex, trackerInformation)

# https://stackoverflow.com/questions/65250690/is-there-a-provably-optimal-block-piece-size-for-torrents-and-individual-file
# Block request lengths decided by client (strategy ig), just pls makes sure they don't overlap
# Remember: 'Blocks' are what're being transmitted, not entire 'pieces' (blocks make up pieces)

# Parses appropriate message types
# peerResp should be a tuple with (length (int), id(int), payload(bytes))
def parsePeerMsg(peerResp, trackerInformation, peer):
    try:
        # print("Raw Peer Message:", peerResp)
        msgLen = peerResp[0]
        if msgLen > 0:
            msgId = peerResp[1]

            match msgId:
                case objects.CHOKE:
                    if objects.DEBUG_MODE:
                        print("Received peerMsg CHOKE -", peer.peerAddr)
                    peer.peerChoked = True

                case objects.UNCHOKE:
                    if objects.DEBUG_MODE:
                        print("Received peerMsg UNCHOKE -", peer.peerAddr)
                    peer.peerChoked = False

                case objects.INTERESTED:
                    if objects.DEBUG_MODE:
                        print("Received peerMsg INTERESTED -", peer.peerAddr)
                    peer.peerInterested = True


                case objects.NOT_INTERESTED:
                    if objects.DEBUG_MODE:
                        print("Received peerMsg NOT_INTERESTED -", peer.peerAddr)
                    peer.peerInterested = False


                case objects.HAVE:
                    if objects.DEBUG_MODE:
                        print("Received peerMsg HAVE -", peer.peerAddr)

                    # Unpacking data
                    pieceIndex = struct.unpack('>I', peerResp[2])[0]

                    peer.set_have(pieceIndex)


                case objects.BITFIELD:
                    if objects.DEBUG_MODE:
                        print("Received peerMsg BITFIELD -", peer.peerAddr)

                    bitfield = BitArray(bytes=peerResp[2])

                    peer.peerBitfield = bitfield


                case objects.REQUEST:
                    if objects.DEBUG_MODE:
                        print("Received peerMsg REQUEST -", peer.peerAddr)

                    # Unpacking data
                    msgBody = struct.unpack('>III', peerResp[2])
                    pieceIndex = msgBody[0]
                    blockIndex = msgBody[1]
                    blockLength = msgBody[2]

                    with open(trackerInformation.name, 'rb') as file:
                        # Move cursor to correct position in file & write
                        file.seek(pieceIndex*trackerInformation.pieceLength + blockIndex)
                        data = file.read(blockLength)

                    if not data or len(data) < blockLength:
                        if objects.DEBUG_MODE:
                            print(f"invalid request. Piece index: {pieceIndex}. Block index: {blockIndex}. Block Length: {blockLength}")
                    else:
                        pieceMsg = objects.messages()
                        pieceMsg.piece((len(data), pieceIndex, blockIndex, data))
                        peer.send_message(pieceMsg)
                    

                case objects.PIECE:
                    if objects.DEBUG_MODE:
                        print("Received peerMsg PIECE -", peer.peerAddr)

                    # Unpacking data
                    msgBody = struct.unpack('>II', peerResp[2][:8])
                    # Piece index = numbers like 0, 1, 2, 3, 4, ...
                    pieceIndex = msgBody[0]
                    # Block index = offset within the piece (by bytes)
                    blockIndex = msgBody[1]
                    blockData = peerResp[2][8:]
                    blockLen = msgLen - 9

                    if objects.DEBUG_MODE:
                        # print("\t- Length - %d\n\t- ID - %d\n\t- pieceIndex - %d\n\t- blockIndex - %d\n\t- blockData -" % (msgLen, msgId, pieceIndex, blockIndex, blockData), blockData)
                        print(f"Length - {msgLen} - ID - {msgId}- pieceIndex - {pieceIndex} - blockIndex - {blockIndex}")
                        print("ACTUAL LENGTH OF BLOCK DATA -", blockLen)

                    # I kind of already handle "strategy" here (handles dup. blocks, finished pieces, etc), but you're free to do more
                    # Bruh this function needs every mutex there is and I hope everythin is Okay
                    with objects.piecesStatusMutex:
                        with objects.piecesCollectionMutex:
                            with objects.trackerReqMutex:
                                addBlockToPiece(pieceIndex, blockIndex, blockData, trackerInformation, blockLen)

                case objects.CANCEL:
                    if objects.DEBUG_MODE:
                        print("Received peerMsg CANCEL -", peer.peerAddr)
                    
                    # Unpacking data
                    msgBody = struct.unpack('>III', peerResp[2])
                    pieceIndex = msgBody[0]
                    blockIndex = msgBody[1]
                    blockLength = msgBody[2]

                    peer.cancelled_request = (pieceIndex, blockIndex, blockLength)

                case objects.PORT: 
                    if objects.DEBUG_MODE:
                        print("Received peerMsg PORT -", peer.peerAddr)
                    # Bro idek if this'll be used b/c it requires a local routing table (yuck!)
        else:
            # keep-alive msg
            # Do stuff
            print("Received peerMsg KEEP-ALIVE -", peer.peerAddr)
    except Exception as e:
        if objects.DEBUG_MODE:
            print("parsePeerMsg exception: {e}")
            traceback.print_exc()

# Read data from existing socket (peer)
# connection is basically the socket (i think...)

# DEPRECATED
# def read(connection, mask, selector, trackerInformation, peer_obj_list):
#     peerResp = connection.recv(4096)  # Data should be ready to recv

    
#     print("Got a message from peer! Socket:", connection)
#     for socket, peer in peer_obj_list:
#         if connection == socket:
#             parsePeerMsg(peerResp, trackerInformation, peer)
                
    # Send whatever you need to back to the peer here
    # connection.send(SOMETHING)

# New client connection
def accept(sock, trackerInformation, peer_obj_list):
    connection, addr = sock.accept()  
    print('accepted', connection, 'from', addr)

    #Extrack IP and Port
    ip, port = addr

    # New clients must send a handshake message. Else, we ignore the connection.
    handshake = connection.recv(68)

     # Check if the received data is a valid BitTorrent handshake
    if len(handshake) != 68 or handshake[1:19] != ("BitTorrent protocol".encode('utf-8')):
        print("Invalid handshake data received. Got this instead: ", connection)
        return None
    
    if (handshake[1+19+8:1+19+28] == objects.trackerRequestMsg.infoHash):
        # Extract the peer_id from the handshake
        peer_id = handshake[48:]

    # Prepare hash and ID to send back a Handshake
    hash_and_id = (objects.trackerRequestMsg.infoHash,objects.trackerRequestMsg.peerId.encode(trackerInformation.encoding))

    # Make a handshake resp 
    handshake_resp = objects.handshake()
    handshake_resp.info_hash = hash_and_id
    handshake_send = (handshake_resp.pstrlen + handshake_resp.pstr + handshake_resp.reserved + handshake_resp.info_hash + handshake_resp.peer_id)
    
    # Handshake resp sent
    connection.send(handshake_send)

    # Get a BitArray with length == num of pieces
    bitfield_array = BitArray(length=len(trackerInformation.pieces))

    # Search for any piece thats completed (where data == None)
    for i, data in objects.piecesStatus.items():
        if data == None:
            bitfield_array.set(True, i)
    
    # Get length of the bitfield bitarray in bytes

    message_length = math.ceil(len(bitfield_array)/8)

    # Pack it into 4 bytes

    length_bytes = struct.pack('>I', message_length)

    bitfield_send = (length_bytes + b'\x05' + bitfield_array.tobytes())

    # Complete sending the bitfield message
    connection.send(bitfield_send)
    
    # Register the connection as a peer!
    # bitfield indicating that this peer has no pieces (all zeroes)
    bitfield = BitArray(length=len(trackerInformation.pieces))

    # Make a new peer, initialize it, and add it to peer_obj_list
    peer = objects.peer()
    peer.peerId = (ip,port,peer_id, bitfield, connection, trackerInformation)
    peer.run_main_logic()

    peer_obj_list.append(peer)
    # connection.setblocking(False)


# Use this method to only recv the correct amount for a message
def get_message_from_sock(sock):
    length_prefix = sock.recv(4)
    if not length_prefix:
        if objects.DEBUG_MODE:
            print(f"didnt receive length prefix for this message from sock: {sock}")
        return None
    
    message_length = struct.unpack(">I", length_prefix)[0]
    print("\t\t- !!!message_length -", message_length)
    if message_length == 0:
        # length 0, id -1, empty bytes (A Stay Alive Message)
        return (0, -1, b'')
    
    # Get message ID and payload
    message_id = sock.recv(1)
    # payload = sock.recv(message_length - 1)
    payload = b''
    while len(payload) < (message_length - 1):
        payload += sock.recv(message_length - 1 - len(payload))
    return (message_length, ord(message_id), payload)


def unchoke_algorithm(peer_obj_list):
    count = 0
    while objects.trackerRequestMsg.left > 0:
        if len(peer_obj_list) > 0:
            try: 
                alivePeers = [peer for peer in peer_obj_list if peer.isAlive]
                count += 1

                # Get interested peers
                interested_peers = [peer for peer in alivePeers if peer.peerInterested]
                # Get the amount to unchoke (could be less than 3)
                num_to_unchoke = max(len(interested_peers), 3)
                download_rates = [get_download_rate(peer) for peer in interested_peers]

                for peer in alivePeers:
                    if peer not in interested_peers:
                        # Just to update download rate for those who are not interested
                        get_download_rate(peer) 
                
                # Zip the download rate with peer objects and sort them
                sorted_dlrs = sorted(zip(download_rates, interested_peers))

                unchoke_msg = objects.messages()
                unchoke_msg.unchoke()
                # Unchoke the top {num_to_unchoke} peers with high download rates
                to_unchoke = list(dict(sorted_dlrs[:num_to_unchoke]).values())
                print(f"TO_UNCHOKE :{to_unchoke}")
                for peer in to_unchoke:
                    peer.amChoking = False
                    peer.send_message(unchoke_msg)
                    print(f"SENT UNCHOKE TO : {peer}")

                # Optimistic Unchoke (every 30 seconds):
                if count == 3:
                    count = 0
                    # candidates are peers that are not already unchoked
                    candidates = [peer for peer in alivePeers if peer not in to_unchoke]
                    if len(candidates) > 0:
                        optimistic_peer = random.choice(candidates)
                        to_unchoke.append(optimistic_peer)
                        optimistic_peer.amChoking = False
                        optimistic_peer.send_message(unchoke_msg)
                        print(f"SENT UNCHOKE TO : {peer}")
                
                # Choke everything that I didn't just unchoked (low download rate peers)
                choke_msg = objects.messages()
                choke_msg.choke()
                for peer in alivePeers:
                    if peer not in to_unchoke and not peer.amChoking:
                        peer.amChoking = True
                        print(f"SENT CHOKE TO : {peer}")
                        peer.send_message(choke_msg)
                # Call every 10 secs
                time.sleep(10)
            except Exception as e:
                if objects.DEBUG_MODE:
                        print(f"exception for unchoke thread\n")
                        print(e)
                        traceback.print_exc()
        else:
            # Wait 10 seconds before trying again if no peer in list
            time.sleep(10)
            count += 1

def listening_thread(args, trackerInformation, peer_obj_list):
     # Establishing listening socket that'll be used for identifying peers joining swarm *_after_* us (we're recving handshake msg)
    listenerSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # Note: The '' represents INADDR_ANY, which is used to bind to all interfaces (https://docs.python.org/3/library/socket.html)
    listenerSocket.bind(('', args.port))
    listenerSocket.listen()

    # Socket observer (poll/select functionality)
    selector = selectors.DefaultSelector()

    # Doc: https://docs.python.org/3/library/selectors.html
    # *** TODO: Find way to register all TCP socket connections with peers to selector object for simult data-reading ***
    selector.register(listenerSocket, selectors.EVENT_READ, data=accept)
    #selector.register(client_socket, selectors.EVENT_READ, data=utils.read)

    # Primary loop for peer communication
    # TODO: Implement unchoke strategy
    while True:
        events = selector.select(timeout=3) # Give this a meaningful timeout value (randomly put 5)

        # Timeout hit
        if not events:
            if objects.DEBUG_MODE:
                print("Timeout reached. Does something idk.")

        # Data in peer socket
        else:
            # Iterate through sockets with data
            for key, mask in events:
                # 'callback' runs the abstracted function set as 'data' parameter when socket was registered
                callback = key.data
                callback(key.fileobj, trackerInformation, peer_obj_list)
    # *** TODO: Find way to get rid of exited client sockets (Working with timeout?) ***
    selector.unregister(trackerSocket)
    selector.close()

def get_download_rate(peer):
    # Get the download rate of the last 10 seconds
    result = (peer.cur_data_downloaded - peer.last_data_downloaded)/10
    # update last_data_downloaded to cur_data_downloaded
    peer.last_data_downloaded = peer.cur_data_downloaded
    return result
