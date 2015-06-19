""" REVISION 19-06-2015 """
# The microscope may be slow to be created; it must wait for the camera, stage
# and the datafile to be ready for use.
import numpy as np
import cv2
import datetime
import time
import abstract_camera
import arduino_stage
import data_file


class Microscope():
    """A class to encapsulate all microscope behaviour.

       It contains a Camera object, a Stage object and a Datafile object; as
       Microscope.camera, Microscope.stage and Microscope.datafile respectively.
       Importing the file allows for use of the class on the command-line, but
       running the file will create a GUI for the microscope to allow interactive
       use. The GUI can be manually started with the run_gui() method."""
    # Key codes for Windows (W) and Linux (L), to allow conversion:
    _GUI_W_KEYS = {2490368: "UP", 2621440: "DOWN", 2424832: "LEFT", 2555904: "RIGHT"}
    _GUI_L_KEYS = {"UP": 82, "DOWN": 84, "LEFT": 81, "RIGHT": 83}
    # Some useful text key code constants to avoid unreadbale code:
    _GUI_KEY_UP = 82
    _GUI_KEY_DOWN = 84
    _GUI_KEY_LEFT = 81
    _GUI_KEY_RIGHT = 83
    _GUI_KEY_SPACE = 32
    _GUI_KEY_ENTER = 13
    # Other useful constants:
    _ARROW_STEP_SIZE = 32
    # Spatial conversions from pixels to microns. This needs to be updated by hand.
    _UM_PER_PIXEL = 0.4846
    # Store a conversion matrix, can be updated with result of calibrate() if necessary.
    _CAMERA_TO_STAGE_MATRIX = np.array([[5.2, 7.0], [6.3, -5.6]])

    def __init__(self, width=640, height=480, cv2camera=False, tty="/dev/ttyACM0", filename=None):
        """Creates a new Microscope containing a Camera and Stage object.

            - Optionally specify a width and height for Camera object,
              the serial port for the Stage object and a filename for the
              attached datafile."""
        # Internal objects needed:
        self.camera = abstract_camera.Camera(width, height, cv2camera)
        self.stage = arduino_stage.Stage(tty)
        self.datafile = data_file.Datafile(filename)
        # Set up the GUI variables:
        self._gui_quit = False
        self._gui_greyscale = True
        self._gui_img = None
        self._gui_pause_img = None
        self._gui_drag_start = None
        self._gui_sel = None
        self._gui_tracking = False
        self._gui_bead_pos = None
        self._gui_colour = (0, 0, 0)  # BGR colour
        # And the rest:
        self.template_selection = None

    def __del__(self):
        # Close the attached objects properly by deleting them
        cv2.destroyAllWindows()
        del self.camera
        del self.stage
        del self.datafile

    def _gui_nothing(self, x):
        """GUI needs callbacks for some functions: this is a blank one."""
        pass

    def _create_gui(self):
        """Initialises the things needed for the GUI."""
        # Create the necessary GUI elements
        cv2.namedWindow('Preview', cv2.WINDOW_AUTOSIZE)
        cv2.namedWindow('Controls', cv2.WINDOW_AUTOSIZE)
        cv2.createTrackbar('Greyscale', 'Controls', 0, 1, self._gui_nothing)
        cv2.createTrackbar('Tracking', 'Controls', 0, 1, self._gui_nothing)
        # Set default values
        cv2.setTrackbarPos('Greyscale', 'Controls', 1)
        cv2.setTrackbarPos('Tracking', 'Controls', 0)
        # Add mouse functionality on image click:
        cv2.setMouseCallback('Preview', self._on_gui_mouse)
        # For the sake of speed, use the RPi iterator:
        self.camera.use_iterator(True)

    def _read_gui_trackbars(self):
        """Read in and process the trackbar values."""
        self._gui_greyscale = bool(cv2.getTrackbarPos('Greyscale', 'Controls'))
        self._gui_tracking = (bool(cv2.getTrackbarPos('Tracking', 'Controls')) and (self._gui_sel is not None) and (self._gui_drag_start is None))

    def _stop_gui_tracking(self):
        """Run the code necessary to cleanup after tracking stopped."""
        self._gui_sel = None
        self._gui_drag_start = None
        self.template_selection = None
        cv2.setTrackbarPos('Tracking', 'Controls', 0)
        self._gui_tracking = False
        self._gui_bead_pos = None

    def _update_gui(self):
        """Run the code needed to update the GUI to latest frame."""
        # Take image if not paused:
        if self._gui_pause_img is None:
            self._gui_img = self.camera.get_frame(greyscale=self._gui_greyscale)
        else:  # If paused, use a fresh copy of the pause frame
            self._gui_img = self._gui_pause_img.copy()
        # Now do the tracking, before the rectangle is drawn!
        if self._gui_tracking:
            self._update_gui_tracker()
        # Now process keyboard input:
        keypress = cv2.waitKey(100)
        # Skip all the unnecessary if statements if no keypress
        if keypress != -1:
            if keypress in self._GUI_W_KEYS:  # This converts Windows arrow keys to Linux
                keypress = self._GUI_L_KEYS[self._GUI_W_KEYS[keypress]]
            else:
                keypress = keypress & 0xFF  # The 0xFF allows ordinary Linux keys to work too
            # Now process the keypress:
            if keypress == ord('q'):  # The q key will quit the GUI and close it
                self._gui_quit = True
            elif keypress == ord('s'):  # The s key will save the box region or the whole frame if nothing selected
                fname = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                if self._gui_sel is None:
                    cv2.imwrite("microscope_img_%s.jpg" % fname, self._gui_img)
                else:
                    w, h = self._gui_sel[2] - self._gui_sel[0], self._gui_sel[3] - self._gui_sel[1]
                    crop = self._gui_img[self._gui_sel[1]:self._gui_sel[1] + h, self._gui_sel[0]:self._gui_sel[0] + w]
                    cv2.imwrite("microscope_img_%s.jpg" % fname, crop)
            elif keypress == ord('t'):  # The t key will save the stored template image
                fname = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                cv2.imwrite("template_%s.jpg" % fname, self.template_selection)
            elif keypress == self._GUI_KEY_SPACE:  # The space bar will reset the template selection box and stop tracking
                self._stop_gui_tracking()
            elif keypress == self._GUI_KEY_RIGHT:  # The arrow keys will move the stage
                self.stage.move_rel([self._ARROW_STEP_SIZE, 0, 0])
            elif keypress == self._GUI_KEY_LEFT:
                self.stage.move_rel([-self._ARROW_STEP_SIZE, 0, 0])
            elif keypress == self._GUI_KEY_UP:
                self.stage.move_rel([0, self._ARROW_STEP_SIZE, 0])
            elif keypress == self._GUI_KEY_DOWN:
                self.stage.move_rel([0, -self._ARROW_STEP_SIZE, 0])
            elif keypress == ord('i'):  # The i key inverts the selection box colour
                if (self._gui_colour == (0, 0, 0)):
                    self._gui_colour = (255, 255, 255)  # White
                else:
                    self._gui_colour = (0, 0, 0)  # Black
        # Finally process the image, drawing boxes etc:
        if self._gui_sel is not None:
            cv2.rectangle(self._gui_img, (self._gui_sel[0], self._gui_sel[1]), (self._gui_sel[2], self._gui_sel[3]), self._gui_colour)
        cv2.imshow('Preview', self._gui_img)

    def _update_gui_tracker(self):
        """Code to update the position of the selection box if tracking is enabled."""
        assert ((self.template_selection is not None) and (self._gui_tracking) and (self._gui_bead_pos is not None))
        w, h = self.template_selection.shape[::-1]
        try:
            if ((w >= 100) or (h >= 100)):  # If the template bigger than the default search box, enlarge it
                D = max(w, h) + 50
                centre = self.camera.find_template(self.template_selection, self._gui_img, self._gui_bead_pos, boxD=D)
            else:
                centre = self.camera.find_template(self.template_selection, self._gui_img, self._gui_bead_pos)
        except RuntimeError:  # find_template raises RuntimeError if region exceeds image bounds:
            self._stop_gui_tracking()  # If this occurs: just stop following it for now!
            return
        self._gui_bead_pos = centre
        x1, y1 = int(centre[0] - w / 2), int(centre[1] - h / 2)  # The template top left corner
        x2, y2 = int(centre[0] + w / 2), int(centre[1] + h / 2)  # The template bottom right corner
        self._gui_sel = (x1, y1, x2, y2)  # The selection is top left to bottom right

    def _on_gui_mouse(self, event, x, y, flags, param):
        """Code to run on mouse action on GUI preview image."""
        # This is the bounding box selection: the start, end and intermediate parts respectively
        if ((event == cv2.EVENT_LBUTTONDOWN) and (self._gui_sel is None)):
            # Pause the display, and set initial coords for the bounding box:
            self._gui_pause_img = self._gui_img
            self._gui_drag_start = (x, y)
            self._gui_sel = (x, y, x, y)
        elif ((event == cv2.EVENT_LBUTTONUP) and (self._gui_drag_start is not None)):
            # Finish setting the bounding box coords and unpause
            self._gui_sel = (min(self._gui_drag_start[0], x), min(self._gui_drag_start[1], y), max(self._gui_drag_start[0], x), max(self._gui_drag_start[1], y))
            w, h = self._gui_sel[2] - self._gui_sel[0], self._gui_sel[3] - self._gui_sel[1]
            self.template_selection = self._gui_pause_img[self._gui_sel[1]:self._gui_sel[1] + h, self._gui_sel[0]:self._gui_sel[0] + w]
            if not self._gui_greyscale:
                self.template_selection = cv2.cvtColor(self.template_selection, cv2.COLOR_BGR2GRAY)
            self._gui_bead_pos = (int((self._gui_sel[0] + self._gui_sel[2]) / 2.0), int((self._gui_sel[1] + self._gui_sel[3]) / 2.0))
            self._gui_pause_img = None
            self._gui_drag_start = None
        elif ((event == cv2.EVENT_MOUSEMOVE) and (self._gui_drag_start is not None) and (flags == cv2.EVENT_FLAG_LBUTTON)):
            # Set the bounding box to some intermediate value; don't unpause.
            self._gui_sel = (min(self._gui_drag_start[0], x), min(self._gui_drag_start[1], y), max(self._gui_drag_start[0], x), max(self._gui_drag_start[1], y))

    def _camera_centre_move(self, template):
        """Code to return the movement in pixels needed to centre a template image,
           as well as the actual camera position of the template."""
        width, height = self.camera._resolution
        template_pos = self.camera.find_template(template, boxD=-1, decimal=True)
        # The camera needs to move (-delta_x, -delta_y); given (0,0) is top left, not centre as needed
        camera_move = (-(template_pos[0] - (width / 2.0)), -(template_pos[1] - (height / 2.0)))
        assert ((camera_move[0] >= -(width / 2.0)) and (camera_move[0] <= (width / 2.0)))
        assert ((camera_move[1] >= -(height / 2.0)) and (camera_move[1] <= (height / 2.0)))
        return (camera_move, template_pos)

    def _camera_move_distance(self, camera_move):
        """Code to convert an (x,y) displacement in pixels to a distance in microns."""
        camera_move = np.array(camera_move)
        assert camera_move.shape == (2,)
        return np.power(np.sum(np.power(camera_move, 2.0)), 0.5) * self._UM_PER_PIXEL

    def centre_on_template(self, template, tolerance=1, max_iterations=10, release=False):
        """Given a template image, move the stage until the template is centred.
           Returns a tuple containing the number of iterations, the camera positions
           and the stage moves as (number, camera_positions, stage_moves),
           where number is returned as -1 * max_iterations if failed to converge.

            - If a tolerance is specified, keep iterating until the template is within
              this distance from the centre or the maximum number of iterations is exceeded.
            - The max_iterations is how many times the code will run to attempt to centre
              the template image to within tolerance before aborting.
            - The stage will be held in position after motion, unless release is
              set to True.
            - A return value for iteration less than zero denotes failure,
              with the absolute value denoting the maximum number of iterations.
               - if centre_on_template(...)[0] < 0 then failure."""
        stage_move = np.array([0, 0, 0])
        stage_moves = []
        camera_move, position = self._camera_centre_move(template)
        camera_positions = [position]
        iteration = 0
        while (((self._camera_move_distance(camera_move)) > tolerance) and (iteration < max_iterations)):
            iteration += 1
            stage_move = np.dot(camera_move, self._CAMERA_TO_STAGE_MATRIX)  # Rotate to stage coords
            stage_move = np.append(stage_move, [0], axis=1)  # Append the z-component of zero
            stage_move = np.trunc(stage_move).astype(int)  # Need integer microsteps (round to zero)
            self.stage.move_rel(stage_move, release=False)
            stage_moves.append(stage_move)
            time.sleep(0.5)
            camera_move, position = self._camera_centre_move(template)
            camera_positions.append(position)
        if release:
            m.stage.release()
        if iteration == max_iterations:
            print "Abort: Tolerance not reached in %d iterations" % iteration
            iteration *= -1
        return (iteration, np.array(camera_positions), np.array(stage_moves))

    def run_gui(self):
        """Run the GUI."""
        self._create_gui()
        while not self._gui_quit:
            self._read_gui_trackbars()
            self._update_gui()
        self.stage.centre_stage()
        cv2.destroyWindow('Preview')
        cv2.destroyWindow('Controls')
        self._gui_quit = False  # This allows restarting of the GUI

    def calibrate(self, template=None, D=128):
        """Calibrate the stage-camera coordinates by finding the transformation between them.

            - If a template is specified, it will be used as the calibration track
              which is searched for in each image. The central half of the image will
              be used if one is not specified.
            - The size of the calibration square can be adjusted using D, in microsteps.
              Care should be taken that the template or central part of the image does
              not leave the field of view!"""
        # Set up the necessary variables:
        self.camera._preview()
#        pos = [np.array([i, j, 0]) for i in [-D, D] for j in [-D, D]]
        pos = [np.array([D, D, 0]), np.array([D, -D, 0]), np.array([-D, -D, 0]), np.array([-D, D, 0])]
        camera_displacement = []
        stage_displacement = []
        # Move to centre from known location to minimise backlash:
        self.stage.move_to_pos([16, 16, 0], release=False)
        self.stage.move_to_pos([0, 0, 0], release=False)
        if template is None:
            template = self.camera.get_frame()
            w, h = template.shape
            template = template[w / 4:3 * w / 4, h / 4:3 * h / 4]
        time.sleep(1)
        # Store the initial configuration:
        init_cam_pos = np.array(self.camera.find_template(template, boxD=-1, decimal=True))
        init_stage_vector = self.stage._pos  # 3 component form
        init_stage_pos = init_stage_vector[0:2]  # xy part
        time.sleep(1)
        # Now make the motions in square specified by pos
        for p in pos:
            self.stage.move_to_pos(np.add(init_stage_vector, p) + np.array([-32, -16, 0]), release=False)  # Backlash correct
            self.stage.move_to_pos(np.add(init_stage_vector, p), release=False)
            time.sleep(1)
            cam_pos = np.array(self.camera.find_template(template, boxD=-1, decimal=True))
            cam_pos = np.subtract(cam_pos, init_cam_pos)
            stage_pos = np.subtract(self.stage._pos[0:2], init_stage_pos)
            camera_displacement.append(cam_pos)
            stage_displacement.append(stage_pos)
        self.stage.centre_stage()
        self.stage.release()
        # Do the required analysis:
        camera_displacement = np.array(camera_displacement)
        camera_displacement -= np.mean(camera_displacement, axis=0)
        print camera_displacement
        stage_displacement = np.array(stage_displacement)
        print stage_displacement
        A, res, rank, s = np.linalg.lstsq(camera_displacement, stage_displacement)
        print "residuals:  ", res
        print "norm:  ", np.linalg.norm(A)
        self.camera._preview()
        return A

if __name__ == "__main__":
    m = Microscope()
    m.run_gui()
