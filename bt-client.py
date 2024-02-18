import socket
import bencodepy
import hashlib
import threading
import utils
import objects
import time
import sys
import datetime
import struct
import random
import selectors
from collections import deque
from bitstring import BitArray

# Large try-catch for graceful disconnect
try:
    # Option parsing
    origStdout = sys.stdout
    args = utils.optParse()

    # Parsing torrent file
    torrentFile = open(args.torrent, "rb")
    metaData = torrentFile.read()

    # Note: the b' prefixing indicates a binary representation since some data could not be read in as string from .torrent file due to utf-8 incompatability 
    # Ask abt Info dictionary using Multiple File Mode
    # utils.printMetaData(metaData)

    # Extracting appropriate data from torrent file for appropriate tracker GET request
    trackerInformation = objects.trackerInfo()
    utils.getTrackerInfo(metaData, trackerInformation)
    utils.parseTorr(metaData, args, objects.trackerRequestMsg)
    decoded = bencodepy.decode(metaData)
    totalBytes = objects.trackerRequestMsg.left

    # Creating empty new file
    file = open(trackerInformation.name, 'w+b')
    file.write(b'')

    # Establishing tracker socket (UDP or TCP)
    trackerSocket = utils.establishSocket(args.udp, trackerInformation)

    # Sending initial trackerRequest / Receiving trackerResponse (TCP sends announce, while UDP sends connect)
    trackerResponse = utils.initConnect(args.udp, trackerSocket, trackerInformation)
    utils.parseTrackerResp(trackerInformation, trackerSocket, trackerResponse, args, objects.trackerResponseMsg)
    
    # Need to do first UDP announce msg here b/c we dont yet know the pdc interval 
    if args.udp:
        trackerResponse = utils.firstUdpAnnounce(trackerSocket, trackerInformation)
        utils.parseTrackerResp(trackerInformation, trackerSocket, trackerResponse, args, objects.trackerResponseMsg, udpAction='announce')

    # objects.trackerResponseMsg.printState()

    # Start detached background threads
    announceThread = threading.Thread(target=utils.pdcTrackerAnnounce, args=(trackerInformation, trackerSocket, args))
    # scrapeThread = threading.Thread(target=utils.pdcTrackerScrape, args=(trackerSocket, trackerInformation))          # Andrei stated that scraping was extra work
    progBarThread = threading.Thread(target=utils.pdcProgressBar, args=(totalBytes, origStdout, trackerInformation, trackerSocket, args))
    announceThread.daemon = True
    # scrapeThread.daemon = True
    progBarThread.daemon = True
    announceThread.start()
    # scrapeThread.start()
    progBarThread.start()

    # add peers and ports to a list where they're stored a tuple (ip addr, port)
    peer_list = []
    for x in objects.trackerResponseMsg.peers:
        toadd = (x["ip"], x["port"])
        #print(toadd)
        peer_list.append(toadd)
    #remove any duplicate ip + port so we don't waste time
    peer_list = list(set(peer_list))
    if objects.DEBUG_MODE:
        print("Peer_list -", peer_list)
    #client_socket = socket.socket(socket.AF_INET,socket.SOCK_STREAM)


    #just make this once in case we need to handshake a different peer
    client_handshake = objects.handshake()
    hash_and_id = (objects.trackerRequestMsg.infoHash,objects.trackerRequestMsg.peerId.encode(trackerInformation.encoding))
    client_handshake.info_hash = hash_and_id

    if objects.DEBUG_MODE:
        print("1st Handshake info:")
        print("\t-pstrlen -", client_handshake.pstrlen)
        print("\t-pstr -", client_handshake.pstr)
        print("\t-reserved -", client_handshake.reserved)
        print("\t-info_hash -", client_handshake.info_hash)
        print("\t-peer_id -", client_handshake.peer_id)


    
    # Populate the work deque with missing pieces indices
    objects.workDeque = deque([i for i in range(len(trackerInformation.pieces))])      

    #for now just use the first peer
    # peer object list
    # peer_obj_list = {}

    # Run choking algorithm after connected to all peers:
    unchokingThread = threading.Thread(target=utils.unchoke_algorithm, args=(objects.peer_obj_list,))
    unchokingThread.daemon = True
    unchokingThread.start()

    # TODO: maybe limit max peers connected? Testing with debian iso doesnt quite work
    for i in range(len(peer_list)):
        print(i)
        try:
            client_socket = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            client_socket.setblocking(False)
            client_socket.settimeout(3) # Timeout for connect
            if objects.DEBUG_MODE:
                print("Trying to connect to", peer_list[i])
            client_socket.connect(peer_list[i])
            if objects.DEBUG_MODE:
                print("Connected to client!", peer_list[i])
            #now we need to pack the handshake into a message to be sent
            handshake_send = (client_handshake.pstrlen + client_handshake.pstr + client_handshake.reserved + client_handshake.info_hash + client_handshake.peer_id)
            if objects.DEBUG_MODE:
                print("Handshake:", handshake_send)
                # print((handshake_send))
            

            client_socket.send(handshake_send)
            client_socket.settimeout(3) # Timeout for receiving handshake
            if objects.DEBUG_MODE:
                print("Trying to recv a handshake from ", peer_list[i])
            handshake_response = client_socket.recv(68)

            try:
                # Try getting a bitfield message
                client_socket.settimeout(5)
                message = utils.get_message_from_sock(client_socket)
                # Set a BitField corresponding to bytes received
                if message[1] == objects.BITFIELD:
                    # Note: do not rely on length of stored bitfield to be == to length of pieces
                    bitfield = BitArray(bytes=message[2])
                else:
                    raise Exception
            except Exception as e:
                if objects.DEBUG_MODE:
                    print("No bitarray received from ", peer_list[i])
                bitfield = BitArray(length=len(trackerInformation.pieces))

            #making sure that the hash of the response is equal to the hash of the handshake!!
            if objects.DEBUG_MODE:
                print("Response:", handshake_response)
                print("Bitfield:", bitfield.bin)
            if (handshake_response[1+19+8:1+19+28] == client_handshake.info_hash):
                #print("they're equal")
                pid = handshake_response[1+19+28:]
                peer = objects.peer()
                ip, port = (peer_list[i])
                peer.peerId = (ip,port,pid, bitfield, client_socket, trackerInformation)
                peerThread = threading.Thread(target= peer.run_main_logic)
                peerThread.daemon = True
                peerThread.start()

                #print(peer.peerAddr)
                #print(peer.peerPort)
                #print(peer.peerId)
                #print(peer.peerChoked)
                #print(peer.peerInterested)
                #print(peer.peerBitfield)
                objects.peer_obj_list.append(peer)
                # send the peer an interested messasge
                # potential_unchoke = client_socket.recv(1024)
                #print("potential unchoke: ", potential_unchoke)
                # if (potential_unchoke == b'\x00\x00\x00\x01\x01'):
                #     if objects.DEBUG_MODE:
                #         print("peer unchoked")
                #     peer.unchoke() #now we can send requests for data blocks from the peer
                # else:
                #     if objects.DEBUG_MODE:
                #         print("Error trying to unchoke")
            # else:
            #if objects.DEBUG_MODE:
            #perhaps_bitfield = client_socket.recv(1024)
                #if the hashresponse didn't match, close the connection
                # client_socket.close()
        except Exception as ex:
            # client_socket.close()
            print(f"Exception caught: {ex}")

    # ********************************************************************************
    # ********************************** FOR PARTNERS ********************************
    # *****  Use the 'objects.trackerResponseMsg' fields to get info about swarm  ****
    # *****     Change 'objects.trackerRequestMsg' fields to reflect changes      ****
    # ** (amount 'uploaded', 'downloaded', and 'left' after interacting with peers) **
    # *******   (I am periodically announcing these changes to the tracker)    *******
    # ********************************************************************************
    #
    # - Note: Whenever attempting to EDIT a shared resource (like 'objects.trackerRequestMsg') in a thread, make sure to acquire the respective MUTEX-LOCK!!!
    #       - The shared objects and mutex-locks are defined at the bottom of objects.py

    # Current functions requesting Mutex: 
    # utils.py/pdcTrackerAnnounce requires trackerRespMutex
    # utils.py/parsePeerMsg requires piecesCollectionMutex, piecesStatusMutex, trackerReqMutex
    # objects.py/peer listen_for_messages, determine_interested, _download_attempt require self._lock
            
    listeningThread = threading.Thread(target=utils.listening_thread, args=(args, trackerInformation, objects.peer_obj_list))
    listeningThread.daemon = True
    listeningThread.start()


    while objects.trackerRequestMsg.left > 0:
        time.sleep(3)

except KeyboardInterrupt:
    if objects.DEBUG_MODE:
        print("\r\n\n------------------------------------------------------------------")
        print("User quit BitTorrent client: Gracefully disconnecting\n")

    objects.trackerRequestMsg.event = 'stopped'
    # Send 'stopped' event msg to disconnect gracefully 
    utils.sendStopped(trackerInformation, trackerSocket, args)