"""
ESP32 Main Program - Serial Command Receiver
Receives servo commands from Mac via USB serial and executes them on PCA9685
"""
import json
import machine
from servo_driver import ServoDriver

# Serial communication setup (USB)
uart = machine.UART(0, baudrate=115200)

# Initialize servo driver
servo_driver = ServoDriver()

def process_command(command_data):
    """Process incoming JSON command and execute servo movement."""
    try:
        command = command_data.get("command")
        
        if command == "move_servo":
            servo_id = command_data["servo_id"]
            angle = command_data["angle"]
            duration = command_data.get("duration", 0.5)
            servo_driver.move_servo(servo_id, angle, duration)
            
        elif command == "move_multiple_servos":
            servos = command_data["servos"]
            for servo_cmd in servos:
                servo_driver.move_servo(
                    servo_cmd["servo_id"],
                    servo_cmd["angle"],
                    servo_cmd.get("duration", 0.5)
                )
                
        elif command == "calibrate_servos":
            servo_driver.calibrate_servos()
            
        elif command == "stop":
            servo_driver.stop_all()
            
        else:
            print(f"Unknown command: {command}")
            
    except Exception as e:
        print(f"Error processing command: {e}")

def main():
    """Main loop - continuously read from serial and process commands."""
    print("ESP32 Servo Controller Ready")
    print("Waiting for commands...")
    
    buffer = ""
    
    while True:
        if uart.any():
            # Read available data
            data = uart.read().decode('utf-8', errors='ignore')
            buffer += data
            
            # Try to find complete JSON commands (ending with newline)
            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                line = line.strip()
                
                if line:
                    try:
                        command_data = json.loads(line)
                        process_command(command_data)
                    except json.JSONDecodeError:
                        print(f"Invalid JSON: {line}")
                    except Exception as e:
                        print(f"Error: {e}")

if __name__ == "__main__":
    main()
