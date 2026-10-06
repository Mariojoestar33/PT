clc;
clear;
close all;

tStart = tic;

% ==============================================================
% DATASET MONOPOLO COBRE
%
% Diferencias clave respecto al script de dipolo:
%   - Antena: monopole() en lugar de dipole()
%   - Longitud de resonancia: lambda/4  (no lambda/2)
%     -> f0 = c / (4*L)
%   - Plano de tierra RECTANGULAR: GroundPlaneLength y
%     GroundPlaneWidth (NO existe GroundPlaneRadius en monopole)
%     Se modela cuadrado con lado = max(3*L, 0.05 m)
%   - Parametro de altura: 'Height' (NO 'Length' como en dipole)
%   - Restriccion de Antenna Toolbox: Width < Height/4
%   - Ventana de frecuencia: +/-50% sobre f0 = c/(4*L)
%   - Validacion de borde identica al script de dipolo
% ==============================================================

c = physconst('LightSpeed');

Z0 = 50;

% ==============================================================
% PARAMETROS PRINCIPALES
% ==============================================================

F_POINTS    = 300;
EDGE_MARGIN = round(0.05 * F_POINTS);   % ~15 puntos

% ==============================================================
% RADIOS DEL CONDUCTOR (identicos al dipolo)
% ==============================================================

a_vals = [
    0.00025
    0.00050
    0.00100
];

% ==============================================================
% MALLA DE LONGITUDES
% El monopolo resuena a lambda/4, por lo que las longitudes
% son la MITAD de las del dipolo para el mismo rango de
% frecuencias (300 MHz - 6 GHz)
%
%   L_monopolo = lambda/4 = c / (4*f)
%
%   @ 6   GHz  -> L ~ 12.5 mm
%   @ 4   GHz  -> L ~ 18.7 mm
%   @ 2   GHz  -> L ~ 37.5 mm
%   @ 1   GHz  -> L ~ 75   mm
%   @ 600 MHz  -> L ~ 125  mm
%   @ 300 MHz  -> L ~ 250  mm
% ==============================================================

% --------------------------------------------------------------
% 4-6 GHz  |  Paso: 0.1 mm
% --------------------------------------------------------------
L1 = 0.0100:0.0001:0.0175;

% --------------------------------------------------------------
% 2-4 GHz  |  Paso: 0.13 mm
% --------------------------------------------------------------
L2 = 0.0177:0.000130:0.0300;

% --------------------------------------------------------------
% 1-2 GHz  |  Paso: 0.5 mm
% --------------------------------------------------------------
L3 = 0.0303:0.0005:0.0600;

% --------------------------------------------------------------
% 600 MHz - 1 GHz  |  Paso: 1 mm
% --------------------------------------------------------------
L4 = 0.0605:0.0010:0.1000;

% --------------------------------------------------------------
% 300 - 600 MHz  |  Paso: 2.5 mm
% --------------------------------------------------------------
L5 = 0.1010:0.0025:0.2500;

% ==============================================================
% CONCATENACION
% ==============================================================

L_vals = [L1 L2 L3 L4 L5];
L_vals = unique(L_vals);
L_vals = L_vals(:);

nL = numel(L_vals);

% ==============================================================
% TOTAL DE SIMULACIONES
% ==============================================================

total_sim = nL * numel(a_vals);

% ==============================================================
% ARCHIVO CSV
% ==============================================================

filename_csv = 'Zin_monopolos_cobre_GHz_optimizado.csv';

if exist(filename_csv, 'file')
    delete(filename_csv);
end

fid = fopen(filename_csv, 'w');
fprintf(fid, 'L,a,f,Rin,Xin\n');
fclose(fid);

% ==============================================================
% MATERIAL
% ==============================================================

copper = metal('Copper');

% ==============================================================
% CONTADORES
% ==============================================================

rows_total = 0;
sim_ok     = 0;
sim_err    = 0;
sim_skip   = 0;
sim_edge   = 0;

fprintf('============================================\n');
fprintf('INICIO SIMULACION - MONOPOLO COBRE\n');
fprintf('============================================\n');
fprintf('Longitudes:             %d\n',   nL);
fprintf('Radios:                 %d\n',   numel(a_vals));
fprintf('Simulaciones estimadas: %d\n',   total_sim);
fprintf('Puntos por barrido:     %d\n',   F_POINTS);
fprintf('Margen de borde:        %d pts\n', EDGE_MARGIN);
fprintf('============================================\n\n');

% ==============================================================
% LOOP PRINCIPAL
% ==============================================================

for i = 1:nL

    L = L_vals(i);

    % ==========================================================
    % FRECUENCIA CENTRAL APROXIMADA
    %
    % El monopolo de longitud L resuena cuando L = lambda/4
    % -> lambda = 4*L  -> f0 = c / (4*L)
    % ==========================================================

    f0 = c / (4 * L);

    % ==========================================================
    % VENTANA DE FRECUENCIA: +/-50% sobre f0
    % El conductor desplaza la resonancia real respecto al
    % valor teorico; el margen del 50% garantiza capturarla
    % ==========================================================

    f_min = 0.50 * f0;
    f_max = 1.50 * f0;

    % Limites globales del sistema
    f_min = max(f_min, 300e6);
    f_max = min(f_max, 6e9);

    if f_min >= f_max
        sim_skip = sim_skip + 1;
        continue;
    end

    f_vals = linspace(f_min, f_max, F_POINTS).';

    % ==========================================================
    % LOOP DE RADIOS
    % ==========================================================

    for j = 1:numel(a_vals)

        a     = a_vals(j);
        ancho = 2 * a;   % Width = diametro del conductor

        % ======================================================
        % VALIDACION GEOMETRICA
        %
        % 1) Conductor delgado valido para MoM: a < 8% de L
        % 2) Limite general de ancho: ancho < 20% de L
        % 3) Restriccion de Antenna Toolbox:
        %       Width < Height/4   (= L/4)
        %    Si no se cumple, monopole() lanza un error.
        % ======================================================

        if a >= 0.08 * L
            sim_skip = sim_skip + 1;
            continue;
        end

        if ancho >= 0.2 * L
            sim_skip = sim_skip + 1;
            continue;
        end

        % Restriccion explicita de Antenna Toolbox
        if ancho >= L / 4
            sim_skip = sim_skip + 1;
            continue;
        end

        try

            % ==================================================
            % LADO DEL PLANO DE TIERRA (plano cuadrado)
            %
            % monopole() usa GroundPlaneLength y GroundPlaneWidth
            % (plano RECTANGULAR), NO GroundPlaneRadius.
            % Se modela cuadrado con lado = max(3*L, 0.05 m).
            % Regla practica: GP >= 2.5*L para aproximar plano
            % infinito. Minimo de 5 cm evita GPs irreales
            % en las longitudes mas cortas del rango.
            % ==================================================

            GP_lado = max(3 * L, 0.05);

            % ==================================================
            % CREACION DE ANTENA
            %
            % Parametros correctos de monopole() segun doc:
            %   'Height'            -> longitud del elemento (L)
            %   'Width'             -> ancho/diametro del conductor
            %   'GroundPlaneLength' -> dimension X del plano
            %   'GroundPlaneWidth'  -> dimension Y del plano
            %   .Conductor          -> objeto metal()
            % ==================================================

            ant = monopole( ...
                'Height',            L,       ...
                'Width',             ancho,   ...
                'GroundPlaneLength', GP_lado, ...
                'GroundPlaneWidth',  GP_lado  ...
            );
            ant.Conductor = copper;

            % ==================================================
            % CALCULO DE IMPEDANCIA
            % ==================================================

            Zin = impedance(ant, f_vals);

            R_vals = real(Zin(:));
            X_vals = imag(Zin(:));

            % ==================================================
            % VALIDACION: minimo de S11 no en el borde
            %
            % Si el minimo cae en los primeros o ultimos
            % EDGE_MARGIN puntos del barrido, la ventana de
            % frecuencia fue insuficiente para esta geometria.
            % Se descarta para no contaminar el dataset con
            % curvas truncadas.
            % ==================================================

            gamma_vals = (R_vals + 1j*X_vals - Z0) ...
                      ./ (R_vals + 1j*X_vals + Z0);

            s11_vals = 20 * log10( ...
                max(abs(gamma_vals), 1e-12) ...
            );

            [~, idx_min_s11] = min(s11_vals);

            if idx_min_s11 <= EDGE_MARGIN || ...
               idx_min_s11 >= (F_POINTS - EDGE_MARGIN)

                sim_edge = sim_edge + 1;
                clear ant Zin R_vals X_vals gamma_vals s11_vals
                continue;

            end

            % ==================================================
            % ESCRITURA CSV
            % ==================================================

            chunk = [ ...
                repmat(L,  F_POINTS, 1), ...
                repmat(a,  F_POINTS, 1), ...
                f_vals, ...
                R_vals, ...
                X_vals  ...
            ];

            writematrix( ...
                chunk, ...
                filename_csv, ...
                'WriteMode', 'append' ...
            );

            rows_total = rows_total + size(chunk, 1);
            sim_ok     = sim_ok + 1;

        catch ME

            sim_err = sim_err + 1;

            % Imprime los primeros 3 errores para diagnostico
            if sim_err <= 3
                fprintf('ERROR [L=%.4f, a=%.5f]: %s\n', ...
                    L, a, ME.message);
            end

        end

        clear ant Zin R_vals X_vals gamma_vals s11_vals

    end

    % ==========================================================
    % PROGRESO
    % ==========================================================

    if mod(i, 20) == 0 || i == nL

        elapsed = toc(tStart) / 60;

        fprintf([ ...
            'Progreso: %4d/%4d (%.1f%%) | ' ...
            'OK: %d | ERR: %d | SKIP: %d | EDGE: %d | ' ...
            'Filas: %d | Tiempo: %.2f min\n'], ...
            i, nL, (i/nL)*100, ...
            sim_ok, sim_err, sim_skip, sim_edge, ...
            rows_total, elapsed);

    end

end

% ==============================================================
% RESUMEN FINAL
% ==============================================================

dur_min = toc(tStart) / 60;

fprintf('\n============================================\n');
fprintf('FIN SIMULACION - MONOPOLO COBRE\n');
fprintf('============================================\n');
fprintf('Archivo CSV:   %s\n',    filename_csv);
fprintf('Sim OK:        %d\n',    sim_ok);
fprintf('Sim Error:     %d\n',    sim_err);
fprintf('Sim Skip:      %d\n',    sim_skip);
fprintf('Sim Edge:      %d\n',    sim_edge);
fprintf('  (resonancia en borde, descartadas)\n');
fprintf('Filas CSV:     %d\n',    rows_total);
fprintf('Tiempo total:  %.2f min\n', dur_min);
fprintf('Tiempo total:  %.2f h\n',   dur_min/60);

if exist(filename_csv, 'file')
    file_info = dir(filename_csv);
    fprintf('Tamano CSV:    %.2f MB\n', file_info.bytes / (1024^2));
end

fprintf('============================================\n');