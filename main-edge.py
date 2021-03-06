
import time, queue, threading, sys, os
import torch, argparse, logging
from pvaccess import Channel
from pvaccess import PvObject
import numpy as np 

from BraggNN import BraggNN
from preprocess import frame_peak_patches_gcenter as frame2patch

class pvaClient:
    def __init__(self, nth=1):
        self.psz = 15
        self.torch_dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.BraggNN  = BraggNN(imgsz=self.psz, fcsz=(16, 8, 4, 2)) # should use the same argu as it in the training.
        mdl_fn = 'models/fc16_8_4_2-sz15.pth'
        self.BraggNN .load_state_dict(torch.load(mdl_fn, map_location=torch.device('cpu')))
        if torch.cuda.is_available():
            self.BraggNN = self.BraggNN.to(self.torch_dev)
        self.frames_processed = 0
        self.base_seq_id = None
        self.recv_frames = 0
        self.tq = queue.Queue()

        for _ in range(nth):
            threading.Thread(target=self.frame_process, daemon=True).start()

    def frame_process(self, ):
        while True:
            pv = self.tq.get()
            frm_id = pv['uniqueId']

            dims = pv['dimension']
            rows = dims[0]['size']
            cols = dims[1]['size']
            frame = pv['value'][0]['ushortValue'].reshape((rows, cols))
            self.frames_processed += 1
            self.tq.task_done()

            tick = time.time()
            patches, patch_ori, big_peaks = frame2patch(frame=frame, psz=self.psz)
            if patches.shape[0] == 0:
                logging.info("%.3f, %d peaks located in frame %d, %.3fms/frame, %d peaks are too big; %d frames processed so far" % (\
                             time.time(), patches.shape[0], frm_id, elapse, big_peaks, self.frames_processed))
            input_tensor = torch.from_numpy(patches[:, np.newaxis].astype('float32'))
            # todo, infer in a batch fashion in case of out-of-memory
            with torch.no_grad():
                pred = self.BraggNN.forward(input_tensor.to(self.torch_dev)).cpu().numpy()
            peak_locs, big_peaks = pred * self.psz + patch_ori, big_peaks

            elapse = 1000 * (time.time() - tick)
            logging.info("%.3f, %d peaks located in frame %d, %.3fms/frame, %d peaks are too big; %d frames processed so far" % (\
                         time.time(), peak_locs.shape[0], frm_id, elapse, big_peaks, self.frames_processed))

    def monitor(self, pv):
        uid = pv['uniqueId']
        if self.base_seq_id is None: self.base_seq_id = uid
        self.recv_frames += 1
        self.tq.put(pv.copy())
        logging.info("%.3f received frame %d, total frame received: %d, should have received: %d" % (time.time(), uid, self.recv_frames, uid - self.base_seq_id + 1))

def main_monitor(ch):
    c = Channel(ch)
    c.setMonitorMaxQueueLength(-1)

    client = pvaClient()

    c.subscribe('monitor', client.monitor)
    c.startMonitor('')

    # ToDo check if it is done from server/detector, where streaming gives signal
    time.sleep(1000)
    c.stopMonitor()
    c.unsubscribe('monitor')

# limitation here: the same frame will be get() over and over again till server got the notification
# def main_get():
#     max_queue_size = -1
#     c = Channel('pvapy:image')
##    c = Channel('13SIM1:Pva1:Image')
#     c.setMonitorMaxQueueLength(max_queue_size)

#     while True:
#         pv = c.get('')
#         uid = pv['uniqueId']
#         print("received frame %d @ %.3f" % (uid, time.time()))
#         dims = pv['dimension']
#         rows = dims[0]['size']
#         cols = dims[1]['size']
#         frame = pv['value'][0]['ushortValue'].reshape((rows, cols))

#         print(frame.shape)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='edge pipeline for Bragg peak finding')
    parser.add_argument('-gpus', type=str, default="0", help='list of visiable GPUs')
    parser.add_argument('-ch',   type=str, default='pvapy:image', help='pva channel name')
    parser.add_argument('-nth',  type=int, default=1, help='number of threads for frame processes')

    args, unparsed = parser.parse_known_args()
    if len(unparsed) > 0:
        print('Unrecognized argument(s): \n%s \nProgram exiting ... ... ' % '\n'.join(unparsed))
        exit(0)
    if len(args.gpus) > 0:
        os.environ['CUDA_VISIBLE_DEVICES'] = args.gpus
    logging.basicConfig(filename='edgeBragg.log', level=logging.DEBUG)
    logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))

    main_monitor(args.ch)
    # main_get()

