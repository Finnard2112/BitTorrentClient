# BitTorrent Client

## Dependencies
```
pip install bencode.py alive-progress bitstring
```  
- Bencode - Lightweight, binary encoding format in the BitTorrent protocol for encoding metadata associated with torrents.
- Alive-progress - Live progress bar for monitoring download.
- Bitstring - Provides bit manipulation for bitfields.

## List of supported features
### Client Options
Run the client script with the -h / --help option to view the entire BitTorrent client option set:  
```
python3 bt-client.py -h
```  

**Mandatory Options**  
*Complete Example:*  
```
python3 bt-client.py -t artofwar.torrent -p 6888
```  
- `-t TORRENT` - Specied .torrent file to parse and file to download
- `-p [1024, 49151]` - Specied port which your client is listening on. BitTorrent ports are *typically* [6881, 6889]

**Optional Options**  
*Complete Example:*  
```
python3 bt-client.py -t artofwar.torrent -p 6888 -c -w 40 -u -d -q
```  
- `-c` - Indicates that the client accepts a compact response from the tracker
- `-w NUMWANT` - Number of peers that the client would like to receive from the tracker. If omitted, defaults to 50 peers.
- `-u` - User may manually opt for support using a UDP-tracker protocol.
- `-d` - Outputs real-time log of client behavior into console for details.
- `-q` - Informs the client to automatically disconnect from the tracker once complete download has finished; otherwise, it will remain part of the swarm as a seeder for peer downloads
