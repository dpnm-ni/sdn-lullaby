import os

import torch
import torch.nn.functional as F
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

from src.dataType import State, Action
from src.animator.animator import Animator
from src.const import VNF_SELECTION_IN_DIM_WITHOUT_SFC_NUM, VNF_PLACEMENT_IN_DIM_WITHOUT_SFC_NUM, MAXIMUM_SFC_NUM


@dataclass
class DebugInfo:
    timestamp: str
    episode: int
    mean_100_step: int
    std_100_step: int
    mean_100_change_slp_srv: float
    std_100_change_slp_srv: float
    mean_100_init_slp_srv: float
    std_100_init_slp_srv: float
    mean_100_final_slp_srv: float
    std_100_final_slp_srv: float
    srv_n: int
    mean_100_change_sfc_in_same_srv: float
    std_100_change_sfc_in_same_srv: float
    mean_100_init_sfc_in_same_srv: float
    std_100_init_sfc_in_same_srv: float
    mean_100_final_sfc_in_same_srv: float
    std_100_final_sfc_in_same_srv: float
    sfc_n: int
    mean_100_exploration: float
    std_100_exploration: float
    mean_100_reward: float
    std_100_reward: float

def setup_mp_env():
    os.environ['CUDA_DEVICE_ORDER'] = 'PCI_BUS_ID'
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    os.environ['OMP_NUM_THREADS'] = '1'

def print_debug_info(debug_info: DebugInfo, refresh: bool = False):
    debug_msg = "[{}] Episode {:05}, Step {:04.2f}\u00B1{:04.2f}, #SleepSrv ({:02.3f}\u00B1{:02.3f})({:02.3f}\u00B1{:02.3f}->{:02.3f}\u00B1{:02.3f}/{}), #SFCinSameSrv ({:02.3f}\u00B1{:02.3f})({:02.3f}\u00B1{:02.3f}->{:02.3f}\u00B1{:02.3f}/{}), Exploration: {:.3f}\u00B1{:02.3f}, Rewards: {:02.3f}\u00B1{:02.3f}".format(
        debug_info.timestamp, debug_info.episode, debug_info.mean_100_step, debug_info.std_100_step,
        debug_info.mean_100_change_slp_srv, debug_info.std_100_change_slp_srv, debug_info.mean_100_init_slp_srv, debug_info.mean_100_init_slp_srv, debug_info.mean_100_final_slp_srv, debug_info.std_100_final_slp_srv, debug_info.srv_n,
        debug_info.mean_100_change_sfc_in_same_srv, debug_info.std_100_change_sfc_in_same_srv, debug_info.mean_100_init_sfc_in_same_srv, debug_info.std_100_init_sfc_in_same_srv, debug_info.mean_100_final_sfc_in_same_srv, debug_info.std_100_final_sfc_in_same_srv, debug_info.sfc_n,
        debug_info.mean_100_exploration, debug_info.std_100_exploration, debug_info.mean_100_reward, debug_info.std_100_reward,
    )
    print(debug_msg, end='\r', flush=True)
    if refresh:
        print('\x1b[2K' + debug_msg, flush=True)

def convert_state_to_vnf_selection_input(state: State, max_vnf_num: int) -> torch.Tensor:
    vnf_selection_input = torch.zeros(max_vnf_num, VNF_SELECTION_IN_DIM_WITHOUT_SFC_NUM + MAXIMUM_SFC_NUM, dtype=torch.float32)

    for vnf in state.vnfs:
        vnf_selection_input[vnf.id] = torch.cat(
            (
                F.one_hot(torch.tensor([vnf.sfc_id]), num_classes=MAXIMUM_SFC_NUM).squeeze(),
                torch.tensor([
                    vnf.cpu_req, vnf.mem_req,
                    state.srvs[vnf.srv_id].cpu_cap, state.srvs[vnf.srv_id].mem_cap,
                    state.srvs[vnf.srv_id].cpu_load, state.srvs[vnf.srv_id].mem_load,
                    state.edge.cpu_cap, state.edge.mem_cap,
                    state.edge.cpu_load, state.edge.mem_load,
                ]),
            ), 
            dim=0,
        )
        
    return vnf_selection_input

def convert_state_to_vnf_placement_input(state: State, vnf_id: int) -> torch.Tensor:
    vnf_placement_input = torch.zeros(len(state.srvs), VNF_PLACEMENT_IN_DIM_WITHOUT_SFC_NUM + MAXIMUM_SFC_NUM, dtype=torch.float32)
    for srv in state.srvs:
        vnf_placement_input[srv.id] = torch.cat(
            (
                F.one_hot(torch.tensor([state.vnfs[vnf_id].sfc_id]), num_classes = MAXIMUM_SFC_NUM).squeeze(),
                torch.tensor([
                    state.vnfs[vnf_id].cpu_req, state.vnfs[vnf_id].mem_req,
                    srv.cpu_cap, srv.mem_cap, srv.cpu_load, srv.mem_load,
                    state.edge.cpu_cap, state.edge.mem_cap, state.edge.cpu_load, state.edge.mem_load
                ]),
            ), 
            dim=0,
        )
    return vnf_placement_input

def get_possible_actions(state: State, max_vnf_num: int) -> Dict[int, List[int]]:
    '''return possible actions for each state

    Args:
        state (State): state

    Returns:
        Dict[int, List[int]]: possible actions
                                    ex) {vnfId: [srvId1, srvId2, ...], vnfId2: [srvId1, srvId2, ...], ...}
    '''
    possible_actions = {}
    
    for vnf_idx in range(max_vnf_num):
        possible_actions[vnf_idx] = []
        if len(state.vnfs) <= vnf_idx: continue
        vnf = state.vnfs[vnf_idx]
        for srv in state.srvs:
            # 이미 설치된 srv로 이동하는 action 막기
            # if vnf.srv_id == srv.id: continue (* WARNING: 사실 종료를 의미하는 것으로 쓰기 위함)
            # capacity 확인
            if srv.cpu_cap - srv.cpu_load < vnf.cpu_req or srv.mem_cap - srv.mem_load < vnf.mem_req: continue
            possible_actions[vnf.id].append(srv.id)
    return possible_actions

def get_info_from_logits(logits: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    probs = logit_to_prob(logits)
    dist = torch.distributions.Categorical(probs=probs)
    actions = dist.sample().to(torch.int32)
    logpas = dist.log_prob(actions)
    is_exploratory = actions != torch.argmax(logits, dim=1)
    return actions, logpas, is_exploratory

def logit_to_prob(logits: torch.Tensor) -> torch.Tensor:
    probs = torch.zeros_like(logits)
    # 0인 값은 prob을 0으로 유지하고, 나머지 값을 확률로 변경
    for i in range(logits.shape[0]):
        probs[i, logits[i] != 0] = torch.softmax(logits[i, logits[i] != 0], dim = 0)
    return probs

def get_device():
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")

def get_zero_util_cnt(state: State) -> int:
    cnt = 0
    for srv in state.srvs:
        if len(srv.vnfs) == 0:
            cnt += 1
    return cnt
    
def get_sfc_cnt_in_same_srv(state: State) -> int:
    cnt = 0
    for sfc in state.sfcs:
        if len(sfc.vnfs) == 0:
            continue
        cnt += 1
        srv_id = sfc.vnfs[0].srv_id
        for vnf in sfc.vnfs:
            if srv_id != vnf.srv_id:
                cnt -= 1
                break
    return cnt

def save_animation(
        srv_n: int, sfc_n: int, vnf_n: int, 
        srv_mem_cap: int, srv_cpu_cap: int, 
        history: List[Tuple[State, Optional[Action]]],
        path=f'./result/anim.mp4'):
    animator = Animator(srv_n=srv_n, sfc_n=sfc_n, vnf_n=vnf_n,
                        srv_mem_cap=srv_mem_cap, srv_cpu_cap=srv_cpu_cap, history=history)
    animator.save(path)
