import math 
import matplotlib.pyplot as plt 
from matplotlib.widgets import Slider 

#arms
L1 = 14.93
L2 = 25.773
L3 = 14.93
L4 = 25.773

#spinny things
OFFSET = 13.538 / 2.0
H1_X = -OFFSET 
H1_Y = 0.0
H2_X = OFFSET
H2_Y = 0.0

def calculate_ik(x, y):
    # brain cell 1
    dx1 = x - H1_X
    dy1 = y - H1_Y
    D1 = math.sqrt(dx1**2 + dy1**2)

    if D1 > (L1 + L2) or D1 < abs(L1 - L2): return None, None 
    beta1 = math.atan2(dy1, dx1)
    alpha1 = math.acos((L1**2 + D1**2 - L2**2) / (2 * L1 * D1))
    theta1_rad = beta1 + alpha1 

    # brain cell 2
    dx2 = x - H2_X
    dy2 = y - H2_Y
    D2 = math.sqrt(dx2**2 + dy2**2)

    if D2 > (L3 + L4) or D2 < abs(L3 - L4): return None, None 
    beta2 = math.atan2(dy2, dx2)
    alpha2 = math.acos((L3**2 + D2**2 - L4**2) / (2 * L3 * D2))
    theta2_rad = beta2 - alpha2 
    return theta1_rad, theta2_rad

fig, ax = plt.subplots(figsize=(8, 8))
plt.subplots_adjust(bottom=0.25) 

init_x = 0.0
init_y = 30.0

line_left, = ax.plot([], [], 'r-o', linewidth=4, markersize=8, label='Left Arm')
line_right, = ax.plot([], [], 'b-o', linewidth=4, markersize=8, label='Right Arm')
target_point, = ax.plot([], [], 'g*', markersize=15, label='Target (J2)')
ax.plot([H1_X, H2_X], [H1_Y, H2_Y], 'k--', linewidth=2)
angle_text = ax.text(0.05, 0.95, '', transform=ax.transAxes, fontsize=12, verticalalignment='top', bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
ax.set_xlim(-50, 50)
ax.set_ylim(-20, 60)
ax.set_aspect('equal', adjustable='box')
ax.grid(True)
ax.legend(loc='upper right')
axcolor = 'lightgoldenrodyellow'
ax_x = plt.axes([0.15, 0.1, 0.65, 0.03], facecolor=axcolor)
ax_y = plt.axes([0.15, 0.05, 0.65, 0.03], facecolor=axcolor)

slider_x = Slider(ax_x, 'X (mm)', -40.0, 40.0, valinit=init_x)
slider_y = Slider(ax_y, 'Y (mm)', 10.0, 45.0, valinit=init_y)

def update(val):
    x = slider_x.val
    y = slider_y.val
    angles = calculate_ik(x, y)
    
    if angles[0] is None:
        target_point.set_color('red')
        target_point.set_data([x], [y])
        angle_text.set_text("Too faar (or too close)") 
        fig.canvas.draw_idle()
        return
        
    target_point.set_color('green')
    theta1, theta2 = angles
    deg1 = math.degrees(theta1)
    deg2 = math.degrees(theta2)
    angle_text.set_text(f'Servo 1: {deg1:.1f}°\nServo 2: {deg2:.1f}°')
    
    J1_x = H1_X + L1 * math.cos(theta1)
    J1_y = H1_Y + L1 * math.sin(theta1)
    J3_x = H2_X + L3 * math.cos(theta2)
    J3_y = H2_Y + L3 * math.sin(theta2)
    
    line_left.set_data([H1_X, J1_x, x], [H1_Y, J1_y, y])
    line_right.set_data([H2_X, J3_x, x], [H2_Y, J3_y, y])
    target_point.set_data([x], [y])
    fig.canvas.draw_idle()

slider_x.on_changed(update)
slider_y.on_changed(update)
update(0) 
plt.show()