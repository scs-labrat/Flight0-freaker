# adsb_server.py

import asyncio
import websockets
import json
import logging
from datetime import datetime

# Import your ADSB encoder and ModeS classes
from ADSBLowLevelEncoder import ADSBLowLevelEncoder
from ModeS import ModeS
from ModeSLocation import ModeSLocation

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize ADS-B Encoder (Singleton pattern ensures one instance)
adsb_encoder = ADSBLowLevelEncoder()

# Initialize ModeS and ModeSLocation
# Replace these parameters with appropriate values as per your simulation
df = 17  # Downlink Format for Aircraft
icao_hex = 'A1B2C3'  # Example ICAO code in hexadecimal
ca = 5  # Capability Code (e.g., 5 for Level 2)

# Convert ICAO from hex string to integer
icao = int(icao_hex, 16)

modes = ModeS(df=df, icao=icao, ca=ca)

# Define the destination for IQ samples
# Example: Write IQ samples to a file
IQ_SAMPLES_FILE = 'iq_samples.bin'

# Asynchronous function to write IQ samples to a file
async def write_iq_samples(iq_samples):
    try:
        # Open the file in append binary mode
        async with asyncio.Lock():
            with open(IQ_SAMPLES_FILE, 'ab') as f:
                f.write(iq_samples)
        logger.info(f"Wrote {len(iq_samples)} IQ samples to {IQ_SAMPLES_FILE}")
    except Exception as e:
        logger.error(f"Failed to write IQ samples: {e}")

# WebSocket Server Handler
async def handle_connection(websocket):
    logger.info(f"Client connected from {websocket.remote_address}")
    try:
        async for message in websocket:
            logger.info(f"Received message: {message}")
            try:
                data = json.loads(message)
                
                # Check if it's an initialization message
                if data.get('type') == 'init':
                    logger.info("Received initialization message from client.")
                    # Send back an acknowledgment
                    ack = {
                        'type': 'init_ack',
                        'status': 'success',
                        'message': 'Server acknowledges connection.'
                    }
                    await websocket.send(json.dumps(ack))
                    logger.info(f"Sent initialization acknowledgment: {ack}")
                    continue  # Proceed to next message

                # Validate message type
                if data.get('message_type') not in ['airborne_position', 'ground_velocity', 'callsign', 'modeA']:
                    logger.warning(f"Unsupported message type: {data.get('message_type')}")
                    response = {
                        'status': 'failure',
                        'reason': f"Unsupported message type: {data.get('message_type')}"
                    }
                    await websocket.send(json.dumps(response))
                    continue

                # Process ADS-B data messages
                adsb_frames = process_adsb_data(data, modes, adsb_encoder)
                
                # Debugging: Log adsb_frames content and type
                logger.debug(f"adsb_frames content: {adsb_frames}")
                logger.debug(f"adsb_frames type: {type(adsb_frames)}")
                
                if adsb_frames:
                    # Ensure adsb_frames is a list of frames
                    if not isinstance(adsb_frames, list):
                        adsb_frames = [adsb_frames]

                    # Prepare lists for hex codes and IQ samples
                    hex_frames = []
                    iq_samples_list = []

                    for frame in adsb_frames:
                        if isinstance(frame, (bytes, bytearray)):
                            # Convert bytes/bytearray to hex string
                            hex_str = frame.hex().upper()
                            hex_frames.append(hex_str)
                            
                            # Convert to IQ samples (assuming modulation function returns bytes)
                            # Here, we'll assume 'frame' is already modulated IQ samples
                            iq_samples = frame  # Modify as per actual modulation
                            iq_samples_list.append(iq_samples)
                        elif isinstance(frame, int):
                            # Convert integer to hex string with leading zeros (assuming 24-bit frames)
                            hex_str = f"{frame:06X}"
                            hex_frames.append(hex_str)
                            
                            # Convert integer to bytes (3 bytes for 24 bits)
                            iq_samples = frame.to_bytes(3, byteorder='big')  # Adjust byte size as needed
                            iq_samples_list.append(iq_samples)
                        else:
                            logger.error("adsb_frames contains unsupported types.")
                            hex_frames = []
                            iq_samples_list = []
                            break

                    # Send hex codes back to client
                    if hex_frames:
                        response = {
                            'status': 'success',
                            'adsb_hex_frames': hex_frames
                        }
                        await websocket.send(json.dumps(response))
                        logger.info(f"Sent ADS-B hex frames back to client: {response}")

                    # Pipe IQ samples to separate destination
                    if iq_samples_list:
                        # Concatenate all IQ samples into a single bytes object
                        concatenated_iq_samples = b''.join(iq_samples_list)
                        # Asynchronously write IQ samples
                        asyncio.create_task(write_iq_samples(concatenated_iq_samples))

                else:
                    response = {
                        'status': 'failure',
                        'reason': 'Invalid ADS-B data'
                    }
                    await websocket.send(json.dumps(response))
                    logger.warning("Failed to process ADS-B data: Invalid ADS-B frames")
            except json.JSONDecodeError:
                logger.error("Received invalid JSON data")
                await websocket.send(json.dumps({'status': 'failure', 'reason': 'Invalid JSON'}))
            except Exception as e:
                logger.error("Error processing message", exc_info=True)
                await websocket.send(json.dumps({'status': 'failure', 'reason': 'Internal server error'}))
    except websockets.exceptions.ConnectionClosed as e:
        logger.info(f"Client disconnected: {e}")
    except Exception as e:
        logger.error("An unexpected error occurred in handle_connection", exc_info=True)

def process_adsb_data(data, modes, encoder):
    """
    Process incoming JSON ADS-B data and generate encoded ADS-B frames.

    Args:
        data (dict): Incoming ADS-B data in JSON format.
        modes (ModeS): Instance of ModeS class for encoding.
        encoder (ADSBLowLevelEncoder): Instance of ADSBLowLevelEncoder class.

    Returns:
        list or bytes or bytearray: Processed ADS-B frames.
    """
    try:
        message_type = data.get('message_type')
        if message_type == 'airborne_position':
            # Extract necessary fields
            lat = float(data['lat'])
            lon = float(data['lon'])
            alt = float(data['altitude'])
            tc = int(data['tc']) if 'tc' in data else 9  # Default to 9 if not provided
            ss = int(data['ss']) if 'ss' in data else 0
            nicsb = int(data['nicsb']) if 'nicsb' in data else 0
            timesync = int(data['timesync']) if 'timesync' in data else 0

            # Encode airborne position
            even_frame, odd_frame = modes.df_encode_airborne_position(
                lat=lat,
                lon=lon,
                alt=alt,
                tc=tc,
                ss=ss,
                nicsb=nicsb,
                timesync=timesync
            )

            # Convert frames to hex strings
            adsb_frames = []
            if even_frame:
                adsb_frames.append(even_frame)
            if odd_frame:
                adsb_frames.append(odd_frame)

            return adsb_frames  # List of bytearrays or bytes

        elif message_type == 'ground_velocity':
            # Extract necessary fields
            ground_velocity_kt = float(data['ground_velocity_kt'])
            track_angle_deg = float(data['track_angle_deg'])
            vertical_rate = float(data['vertical_rate'])

            # Encode ground velocity
            ground_velocity_frame = modes.df_encode_ground_velocity(
                ground_velocity_kt=ground_velocity_kt,
                track_angle_deg=track_angle_deg,
                vertical_rate=vertical_rate
            )

            # Add to frames list if encoding was successful
            if ground_velocity_frame:
                return [ground_velocity_frame]  # List of bytearrays or bytes

            return None

        elif message_type == 'callsign':
            # Extract necessary fields
            callsign = data['callsign']

            # Encode callsign
            callsign_frame = modes.callsign_encode(callsign=callsign)

            # Add to frames list if encoding was successful
            if callsign_frame:
                return [callsign_frame]  # List of bytearrays or bytes

            return None

        elif message_type == 'modeA':
            # Extract necessary fields
            modeA_code = data['modeA_code']
            emergency_state = int(data.get('emergency_state', 0))

            # Encode Mode A
            modeA_frame = modes.modaA_encode(
                modeA_4096_code=modeA_code,
                emergency_state=emergency_state
            )

            # Add to frames list if encoding was successful
            if modeA_frame:
                return [modeA_frame]  # List of bytearrays or bytes

            return None

        else:
            logger.warning(f"Unsupported message type: {message_type}")
            return None

    except KeyError as e:
        logger.error(f"Missing key in data: {e}")
        return None
    except Exception as e:
        logger.error(f"Error processing ADS-B data: {e}")
        return None

async def main():
    # Define server host and port
    host = 'localhost'
    port = 8080

    logger.info(f"Starting ADS-B WebSocket server on ws://{host}:{port}")

    # Start the WebSocket server
    async with websockets.serve(handle_connection, host, port):
        logger.info("ADS-B WebSocket server started and listening for connections.")
        await asyncio.Future()  # Run forever

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("ADS-B WebSocket server stopped manually.")
